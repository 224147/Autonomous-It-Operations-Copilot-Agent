"""Key/value + queue abstraction used for agent state, caching, sessions, signals and the workflow queue.

RedisKV is used when REDIS_URL is set. MemoryKV is a single-process fallback for development and tests.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from typing import Any, Protocol


class KeyValueStore(Protocol):
    def ping(self) -> bool: ...
    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    def get_json(self, key: str) -> Any | None: ...
    def delete(self, key: str) -> None: ...
    def incr(self, key: str, ttl: int | None = None) -> int: ...
    def list_push(self, key: str, value: Any, max_len: int) -> None: ...
    def list_range(self, key: str, limit: int) -> list[Any]: ...
    def set_add(self, key: str, member: str) -> None: ...
    def set_members(self, key: str) -> set[str]: ...
    def enqueue(self, queue: str, item: Any) -> None: ...
    def dequeue(self, queue: str, timeout: float = 0) -> Any | None: ...
    def queue_length(self, queue: str) -> int: ...


class MemoryKV:
    backend = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, tuple[str, float | None]] = {}
        self._lists: dict[str, deque[str]] = defaultdict(deque)
        self._sets: dict[str, set[str]] = defaultdict(set)
        self._queues: dict[str, deque[str]] = defaultdict(deque)

    def ping(self) -> bool:
        return True

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        with self._lock:
            self._values[key] = (json.dumps(value, default=str), time.time() + ttl if ttl else None)

    def _live(self, key: str) -> str | None:
        item = self._values.get(key)
        if item is None:
            return None
        raw, expires = item
        if expires is not None and expires < time.time():
            del self._values[key]
            return None
        return raw

    def get_json(self, key: str) -> Any | None:
        with self._lock:
            raw = self._live(key)
        return None if raw is None else json.loads(raw)

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._lists.pop(key, None)
            self._sets.pop(key, None)

    def incr(self, key: str, ttl: int | None = None) -> int:
        with self._lock:
            raw = self._live(key)
            current = int(raw) if raw is not None else 0
            expires = self._values[key][1] if raw is not None else (time.time() + ttl if ttl else None)
            self._values[key] = (str(current + 1), expires)
            return current + 1

    def list_push(self, key: str, value: Any, max_len: int) -> None:
        with self._lock:
            items = self._lists[key]
            items.appendleft(json.dumps(value, default=str))
            while len(items) > max_len:
                items.pop()

    def list_range(self, key: str, limit: int) -> list[Any]:
        with self._lock:
            raw = list(self._lists.get(key, ()))[:limit]
        return [json.loads(r) for r in raw]

    def set_add(self, key: str, member: str) -> None:
        with self._lock:
            self._sets[key].add(member)

    def set_members(self, key: str) -> set[str]:
        with self._lock:
            return set(self._sets.get(key, set()))

    def enqueue(self, queue: str, item: Any) -> None:
        with self._lock:
            self._queues[queue].append(json.dumps(item, default=str))

    def dequeue(self, queue: str, timeout: float = 0) -> Any | None:
        deadline = time.time() + timeout
        while True:
            with self._lock:
                if self._queues[queue]:
                    return json.loads(self._queues[queue].popleft())
            if time.time() >= deadline:
                return None
            time.sleep(0.05)

    def queue_length(self, queue: str) -> int:
        with self._lock:
            return len(self._queues[queue])


class RedisKV:
    backend = "redis"

    def __init__(self, url: str = "", client=None) -> None:  # noqa: ANN001
        import redis

        self._r = client or redis.Redis.from_url(url, decode_responses=True, socket_timeout=10, health_check_interval=30)

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._r.set(key, json.dumps(value, default=str), ex=ttl)

    def get_json(self, key: str) -> Any | None:
        raw = self._r.get(key)
        return None if raw is None else json.loads(raw)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def incr(self, key: str, ttl: int | None = None) -> int:
        pipe = self._r.pipeline()
        pipe.incr(key)
        if ttl:
            pipe.expire(key, ttl, nx=True)
        return int(pipe.execute()[0])

    def list_push(self, key: str, value: Any, max_len: int) -> None:
        pipe = self._r.pipeline()
        pipe.lpush(key, json.dumps(value, default=str))
        pipe.ltrim(key, 0, max_len - 1)
        pipe.execute()

    def list_range(self, key: str, limit: int) -> list[Any]:
        return [json.loads(r) for r in self._r.lrange(key, 0, limit - 1)]

    def set_add(self, key: str, member: str) -> None:
        self._r.sadd(key, member)

    def set_members(self, key: str) -> set[str]:
        return set(self._r.smembers(key))

    def enqueue(self, queue: str, item: Any) -> None:
        self._r.rpush(queue, json.dumps(item, default=str))

    def dequeue(self, queue: str, timeout: float = 0) -> Any | None:
        if timeout > 0:
            res = self._r.blpop([queue], timeout=max(1, int(timeout)))
            raw = res[1] if res else None
        else:
            raw = self._r.lpop(queue)
        return None if raw is None else json.loads(raw)

    def queue_length(self, queue: str) -> int:
        return int(self._r.llen(queue))


def create_kv(redis_url: str) -> KeyValueStore:
    return RedisKV(redis_url) if redis_url else MemoryKV()
