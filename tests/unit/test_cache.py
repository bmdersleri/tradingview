from pathlib import Path

from tvcli.cache import SQLiteTTLCache


def test_cache_round_trip_and_stats(tmp_path: Path) -> None:
    now = [1000.0]
    cache = SQLiteTTLCache(tmp_path / "cache.sqlite3", clock=lambda: now[0])

    cache.set("alpha", {"answer": 42}, ttl_seconds=60)

    assert cache.get("alpha") == {"answer": 42}
    assert cache.stats()["entries"] == 1
    assert cache.stats()["hits"] == 1

    now[0] = 2000.0
    assert cache.get("alpha") is None
    assert cache.purge() == 0


def test_cache_clear(tmp_path: Path) -> None:
    cache = SQLiteTTLCache(tmp_path / "cache.sqlite3")
    cache.set("alpha", "value", ttl_seconds=60)
    cache.clear()

    assert cache.get("alpha") is None
