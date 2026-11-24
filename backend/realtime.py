"""Real-time event fan-out to the frontend (Socket.IO). Publishing is a no-op until the server is attached."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import socketio

from backend.config import Settings

logger = logging.getLogger(__name__)

ROOM = "operators"


class EventBus:
    def __init__(self, settings: Settings) -> None:
        manager = None
        if settings.redis_url:
            manager = socketio.AsyncRedisManager(settings.redis_url)  # fan-out across replicas
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=settings.cors_origin_list,
            client_manager=manager,
            logger=False,
            engineio_logger=False,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self.token_validator = None  # set by the app: callable(token) -> user | None
        self._register()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _register(self) -> None:
        @self.sio.event
        async def connect(sid, environ, auth):  # noqa: ANN001
            token = (auth or {}).get("token") if isinstance(auth, dict) else None
            user = self.token_validator(token) if (self.token_validator and token) else None
            if user is None:
                raise socketio.exceptions.ConnectionRefusedError("authentication required")
            await self.sio.enter_room(sid, ROOM)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        """Thread-safe publish; safe to call from sync services and worker threads."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.sio.emit(event, payload, room=ROOM), loop)
        except Exception:
            logger.exception("Failed to publish event %s", event)
