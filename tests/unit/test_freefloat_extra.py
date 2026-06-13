from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import httpx
import pytest

from tvcli.cache import SQLiteTTLCache
from tvcli.errors import NetworkError, NotFoundError, UpstreamChangedError
from tvcli.layers import freefloat as ff
from tvcli.ratelimit import SQLiteTokenBucket


def test_cell_value_inline_string() -> None:
    # Test _cell_value inline string handling
    # Create cell with <is><t>foo</t><t>bar</t></is>
    cell = ET.Element("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c")
    inline = ET.SubElement(
        cell, "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is"
    )
    t1 = ET.SubElement(
        inline, "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
    )
    t1.text = "foo"
    t2 = ET.SubElement(
        inline, "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
    )
    t2.text = "bar"

    val = ff._cell_value(cell, [])
    assert val == "foobar"


def test_to_float_value_error() -> None:
    assert ff._to_float("not a float") == 0.0
    assert ff._to_float("") == 0.0


def test_format_date_and_today() -> None:
    d = date(2026, 6, 12)
    assert ff._format_date(d) == "12/06/2026"
    assert isinstance(ff._today(), date)


def test_download_report_as_fid_not_found(monkeypatch) -> None:
    # Mock httpx.Client to return HTML without as_fid
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, **kwargs):
            req = httpx.Request("GET", url)
            return httpx.Response(200, text="<html>no token here</html>", request=req)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "Client", MockClient)
    with pytest.raises(UpstreamChangedError) as exc_info:
        ff._download_report(date(2026, 6, 12))
    assert "as_fid" in str(exc_info.value)


def test_download_report_network_error(monkeypatch) -> None:
    # Mock httpx.Client to raise HTTPError to test retries and NetworkError
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, **kwargs):
            raise httpx.ConnectError("Connection timed out")

    monkeypatch.setattr(httpx, "Client", MockClient)
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # skip sleep

    with pytest.raises(NetworkError) as exc_info:
        ff._download_report(date(2026, 6, 12))
    assert "Unable to fetch the VAP free-float report" in str(exc_info.value)


def test_download_report_not_found(monkeypatch) -> None:
    # Mock httpx.Client to return non-zip content to trigger NotFoundError
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, **kwargs):
            req = httpx.Request("GET", url)
            return httpx.Response(200, text='name="as_fid" value="12345"', request=req)

        def post(self, url, **kwargs):
            req = httpx.Request("POST", url)
            # non-PK content
            return httpx.Response(200, content=b"HTML content from VAP", request=req)

    monkeypatch.setattr(httpx, "Client", MockClient)
    with pytest.raises(NotFoundError) as exc_info:
        ff._download_report(date(2026, 6, 12))
    assert "has no free-float report" in str(exc_info.value)


def test_fetch_report_auto_lookback_not_found(monkeypatch, tmp_path: Path) -> None:
    # Mock _fetch_for_date to always raise NotFoundError
    def mock_fetch_for_date(*args, **kwargs):
        raise NotFoundError("Not found for date")

    monkeypatch.setattr(ff, "_fetch_for_date", mock_fetch_for_date)
    cache = SQLiteTTLCache(tmp_path / "c.sqlite3")
    throttle = SQLiteTokenBucket(
        tmp_path / "c.sqlite3", capacity=5, refill_per_second=1
    )

    with pytest.raises(NotFoundError) as exc_info:
        ff.fetch_report(None, cache=cache, throttle=throttle)
    assert "No VAP free-float report found in the last week" in str(exc_info.value)


def test_archive_lookup_exception(monkeypatch) -> None:
    # Mock ArchiveStore to raise an exception, verifying it returns None
    class BadArchiveStore:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Database corrupt")

    # Monkeypatch where ArchiveStore is imported inside freefloat_archive
    monkeypatch.setattr("tvcli.layers.freefloat_archive.ArchiveStore", BadArchiveStore)
    assert ff._archive_lookup("THYAO", date(2026, 6, 12)) is None
