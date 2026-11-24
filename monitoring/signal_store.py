"""Bounded store of recent logs, metrics and traces per service (Redis-backed when available).

Long-term retention lives in the observability backends (Loki, Prometheus, Jaeger, ...); the platform only
keeps the recent window it needs for detection, correlation and verification.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.common.timeutil import iso, parse_ts, utcnow
from backend.kv import KeyValueStore

KINDS = ("log", "metric", "trace")
SERVICES_KEY = "sig:services"


class SignalStore:
    def __init__(self, kv: KeyValueStore, retention_per_series: int = 3000) -> None:
        self._kv = kv
        self._retention = retention_per_series

    @staticmethod
    def _key(kind: str, service: str) -> str:
        return f"sig:{kind}:{service}"

    def add(self, kind: str, signals: list[dict[str, Any]]) -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown signal kind {kind!r}")
        count = 0
        for signal in signals:
            service = str(signal.get("service") or "").strip().lower()
            if not service:
                continue
            signal = {**signal, "service": service, "kind": kind}
            signal["timestamp"] = iso(parse_ts(signal.get("timestamp")))
            self._kv.list_push(self._key(kind, service), signal, self._retention)
            self._kv.set_add(SERVICES_KEY, service)
            count += 1
        return count

    def services(self) -> list[str]:
        return sorted(self._kv.set_members(SERVICES_KEY))

    def query(
        self,
        kind: str,
        service: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return signals in chronological order inside [since, until]."""
        items = self._kv.list_range(self._key(kind, service.lower()), self._retention)
        out = []
        for item in items:
            ts = parse_ts(item.get("timestamp"))
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            out.append(item)
        out.sort(key=lambda s: s["timestamp"])
        return out[-limit:] if limit else out

    def recent_window(self, service: str, minutes: int) -> dict[str, list[dict[str, Any]]]:
        since = utcnow().timestamp() - minutes * 60
        since_dt = parse_ts(since)
        return {f"{kind}s": self.query(kind, service, since=since_dt) for kind in KINDS}

    def latest_metric(self, service: str, name: str, since: datetime | None = None) -> dict[str, Any] | None:
        series = [m for m in self.query("metric", service, since=since) if m.get("name") == name]
        return series[-1] if series else None
