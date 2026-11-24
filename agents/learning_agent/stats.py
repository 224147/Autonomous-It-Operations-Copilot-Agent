"""Learning statistics: per (category, action) success counters, persisted in the KV store (Redis in production)."""
from __future__ import annotations

from backend.kv import KeyValueStore


class ActionStats:
    def __init__(self, kv: KeyValueStore) -> None:
        self._kv = kv

    @staticmethod
    def _key(category: str, action: str) -> str:
        return f"learn:action:{category}:{action}"

    def get(self, category: str, action: str) -> tuple[int, int]:
        data = self._kv.get_json(self._key(category, action)) or {}
        return int(data.get("success", 0)), int(data.get("total", 0))

    def record(self, category: str, action: str, success: bool) -> None:
        success_n, total = self.get(category, action)
        self._kv.set_json(self._key(category, action), {"success": success_n + int(success), "total": total + 1})
