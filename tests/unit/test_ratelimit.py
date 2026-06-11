from pathlib import Path

from tvcli.ratelimit import SQLiteTokenBucket


def test_token_bucket_allows_until_capacity(tmp_path: Path) -> None:
    now = [1000.0]
    bucket = SQLiteTokenBucket(
        tmp_path / "tokens.sqlite3",
        capacity=2.0,
        refill_per_second=1.0,
        clock=lambda: now[0],
    )

    assert bucket.allow("global") is True
    assert bucket.allow("global") is True
    assert bucket.allow("global") is False

    now[0] += 2.0
    assert bucket.allow("global") is True
