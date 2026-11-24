"""KeyValueStore contract: identical behaviour for the in-memory and Redis implementations."""
from __future__ import annotations

import time

import fakeredis
import pytest

from backend.kv import MemoryKV, RedisKV


@pytest.fixture(params=["memory", "redis"])
def kv(request):
    return MemoryKV() if request.param == "memory" else RedisKV(client=fakeredis.FakeRedis(decode_responses=True))


def test_json_values_ttl_and_delete(kv):
    kv.set_json("a", {"x": [1, 2]})
    assert kv.get_json("a") == {"x": [1, 2]} and kv.get_json("missing") is None
    kv.set_json("t", 1, ttl=1)
    time.sleep(1.2)
    assert kv.get_json("t") is None
    kv.delete("a")
    assert kv.get_json("a") is None
    assert kv.ping() is True


def test_counter_with_ttl_is_atomic_and_expires(kv):
    assert [kv.incr("c", ttl=1) for _ in range(3)] == [1, 2, 3]
    time.sleep(1.2)
    assert kv.incr("c", ttl=1) == 1


def test_capped_list_newest_first_and_sets(kv):
    for i in range(5):
        kv.list_push("l", {"i": i}, max_len=3)
    assert [x["i"] for x in kv.list_range("l", 10)] == [4, 3, 2]
    kv.set_add("s", "a"), kv.set_add("s", "a"), kv.set_add("s", "b")
    assert kv.set_members("s") == {"a", "b"}


def test_fifo_queue(kv):
    kv.enqueue("q", {"n": 1}), kv.enqueue("q", {"n": 2})
    assert kv.queue_length("q") == 2
    assert kv.dequeue("q")["n"] == 1 and kv.dequeue("q")["n"] == 2 and kv.dequeue("q") is None
