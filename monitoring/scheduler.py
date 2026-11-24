"""Background loops: connector polling, detection sweep, approval expiry, auto-resolution and gauge refresh."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from agents.common.ports import PlatformData, SignalConnector
from agents.common.timeutil import parse_ts, utcnow
from backend.config import Settings
from backend.kv import KeyValueStore
from monitoring.alert_ingestion.service import IncidentTrigger
from monitoring.signal_store import SignalStore

logger = logging.getLogger(__name__)


class ConnectorPoller:
    """Pulls metrics/logs from configured observability backends into the signal store."""

    def __init__(self, connectors: list[SignalConnector], platform: PlatformData, signals: SignalStore,
                 kv: KeyValueStore, lookback_minutes: int = 5) -> None:
        self._connectors, self._platform, self._signals, self._kv = connectors, platform, signals, kv
        self._lookback = lookback_minutes

    def poll_once(self) -> int:
        total = 0
        now = utcnow()
        for connector in self._connectors:
            key = f"poll:last:{connector.name}"
            last = self._kv.get_json(key)
            since = parse_ts(last) if last else now - timedelta(minutes=self._lookback)
            for service in self._platform.services():
                try:
                    pulled = connector.fetch(service, since, now)
                except Exception as exc:
                    logger.warning("poll %s/%s failed: %s", connector.name, service["name"], exc)
                    continue
                for kind, key_name in (("log", "logs"), ("metric", "metrics"), ("trace", "traces")):
                    total += self._signals.add(kind, pulled.get(key_name, []))
            self._kv.set_json(key, now.isoformat())
        return total


class Scheduler:
    def __init__(self, settings: Settings, trigger: IncidentTrigger, poller: ConnectorPoller | None,
                 maintenance: list[Callable[[], object]], kv: KeyValueStore) -> None:
        self._s, self._trigger, self._poller, self._maintenance, self._kv = settings, trigger, poller, maintenance, kv
        self._tasks: list[asyncio.Task] = []

    def _leader(self, name: str, interval: float) -> bool:
        """With several replicas only one runs each scheduled task per interval (Redis-backed lock)."""
        return self._kv.incr(f"sched:{name}", ttl=max(int(interval) - 1, 1)) == 1

    async def _loop(self, name: str, interval: float, fn: Callable[[], object]) -> None:
        await asyncio.sleep(min(interval, 5))
        while True:
            try:
                if await asyncio.to_thread(self._leader, name, interval):
                    await asyncio.to_thread(fn)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler task %s failed", name)
            await asyncio.sleep(interval)

    def start(self) -> None:
        if self._poller:
            self._tasks.append(asyncio.create_task(self._loop("poll", self._s.poll_interval_seconds, self._poller.poll_once)))
        self._tasks.append(asyncio.create_task(self._loop("sweep", self._s.detection_sweep_seconds, self._trigger.sweep)))

        def maintain() -> None:
            for fn in self._maintenance:
                try:
                    fn()
                except Exception:
                    logger.exception("maintenance task failed")

        self._tasks.append(asyncio.create_task(self._loop("maintenance", 30, maintain)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
