from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from tvcli.cache import SQLiteTTLCache
from tvcli.errors import RateLimitedError, UpstreamChangedError
from tvcli.layers import freefloat as ff
from tvcli.ratelimit import SQLiteTokenBucket

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _make_xlsx(rows: list[list[str]], *, header: list[str] | None = None) -> bytes:
    """Build a minimal .xlsx matching the VAP layout (title row, header, data)."""
    header = header or [
        "Tarih",
        "ISIN",
        "ISIN Açıklama",
        "Borsa Kodu",
        "İhraççı Üye",
        "Fiili Dolaşımdaki Pay Adedi",
        "İhraççı Sermaye",
        "Fiili Pay/Sermaye Oranı (%)",
    ]
    # Shared strings: title + header + every non-numeric cell.
    strings: list[str] = ["MKK Fiili Dolaşım Raporu"]
    strings.extend(header)
    sid = {s: i for i, s in enumerate(strings)}

    def intern(text: str) -> int:
        if text not in sid:
            sid[text] = len(strings)
            strings.append(text)
        return sid[text]

    cols = "ABCDEFGH"
    sheet_rows = [
        '<row r="1"><c r="A1" t="s"><v>0</v></c></row>',
        '<row r="3">'
        + "".join(
            f'<c r="{cols[i]}3" t="s"><v>{intern(h)}</v></c>'
            for i, h in enumerate(header)
        )
        + "</row>",
    ]
    rnum = 4
    for row in rows:
        cells = []
        for i, val in enumerate(row):
            ref = f"{cols[i]}{rnum}"
            # Columns F, G, H are numeric; the rest are shared strings.
            if i in (5, 6, 7):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{intern(val)}</v></c>')
        sheet_rows.append(f'<row r="{rnum}">' + "".join(cells) + "</row>")
        rnum += 1

    shared_xml = (
        f'<sst xmlns="{_NS}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in strings)
        + "</sst>"
    )
    sheet_xml = (
        f'<worksheet xmlns="{_NS}"><sheetData>'
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", shared_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


_SAMPLE_ROWS = [
    [
        "11.06.2026",
        "TRATHYAO91M5",
        "TÜRK HAVA YOLLARI",
        "THYAO",
        "TRTHY",
        "696026808",
        "1380000000",
        "50.43",
    ],
    [
        "11.06.2026",
        "TREENPRA0001",
        "ENPRA ŞIRKET",
        "ENPRA",
        "TRENP",
        "12000",
        "10000000",
        "0.12",
    ],
]


def test_parse_xlsx_extracts_records() -> None:
    records = ff._parse_xlsx(_make_xlsx(_SAMPLE_ROWS))
    assert len(records) == 2
    thy = records[0]
    assert thy.code == "THYAO"
    assert thy.name == "TÜRK HAVA YOLLARI"
    assert thy.ratio == pytest.approx(50.43)
    assert thy.float_shares == pytest.approx(696026808.0)
    assert records[1].code == "ENPRA"
    assert records[1].ratio == pytest.approx(0.12)


def test_parse_xlsx_rejects_changed_columns() -> None:
    bad_header = ["X", "Y", "Z", "Borsa Kodu", "E", "F", "G", "H"]
    with pytest.raises(UpstreamChangedError):
        ff._parse_xlsx(_make_xlsx(_SAMPLE_ROWS, header=bad_header))


def test_parse_xlsx_rejects_non_xlsx() -> None:
    with pytest.raises(UpstreamChangedError):
        ff._parse_xlsx(b"not a zip file")


def test_normalize_code() -> None:
    assert ff.normalize_code("BIST:THYAO") == "THYAO"
    assert ff.normalize_code("thyao") == "THYAO"
    assert ff.normalize_code(" bist:a1cap ") == "A1CAP"


def test_is_bist_symbol() -> None:
    assert ff.is_bist_symbol("BIST:THYAO") is True
    assert ff.is_bist_symbol("THYAO") is True  # bare ticker assumes BIST
    assert ff.is_bist_symbol("NASDAQ:AAPL") is False


def _patch_download(monkeypatch, calls: list[int]) -> None:
    def fake_download(report_date: date) -> tuple[ff.FloatRecord, ...]:
        calls.append(1)
        return ff._parse_xlsx(_make_xlsx(_SAMPLE_ROWS))

    monkeypatch.setattr(ff, "_download_report", fake_download)


def test_fetch_report_caches(monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []
    _patch_download(monkeypatch, calls)
    cache = SQLiteTTLCache(tmp_path / "c.sqlite3")
    throttle = SQLiteTokenBucket(
        tmp_path / "c.sqlite3", capacity=5, refill_per_second=1
    )

    first = ff.fetch_report(date(2026, 6, 11), cache=cache, throttle=throttle)
    second = ff.fetch_report(date(2026, 6, 11), cache=cache, throttle=throttle)

    assert {r.code for r in first} == {"THYAO", "ENPRA"}
    assert first == second
    assert len(calls) == 1  # second call served from cache, no re-download


def test_fetch_report_rate_limited(monkeypatch, tmp_path: Path) -> None:
    _patch_download(monkeypatch, [])
    cache = SQLiteTTLCache(tmp_path / "c.sqlite3")
    # Zero-capacity bucket => first uncached fetch is throttled.
    throttle = SQLiteTokenBucket(
        tmp_path / "c.sqlite3", capacity=0, refill_per_second=0
    )
    with pytest.raises(RateLimitedError):
        ff.fetch_report(date(2026, 6, 11), cache=cache, throttle=throttle)


def test_lookup(monkeypatch, tmp_path: Path) -> None:
    _patch_download(monkeypatch, [])
    cache = SQLiteTTLCache(tmp_path / "c.sqlite3")
    throttle = SQLiteTokenBucket(
        tmp_path / "c.sqlite3", capacity=5, refill_per_second=1
    )
    found = ff.lookup("BIST:THYAO", date(2026, 6, 11), cache=cache, throttle=throttle)
    assert found is not None
    assert found.code == "THYAO"
    missing = ff.lookup("NOPE", date(2026, 6, 11), cache=cache, throttle=throttle)
    assert missing is None


def _point_archive_at(monkeypatch, tmp_path: Path):
    """Redirect the archive's default DB path into tmp for isolation."""
    db = tmp_path / "archive.sqlite3"
    monkeypatch.setattr(
        "tvcli.layers.freefloat_archive.default_archive_path", lambda: db
    )
    from tvcli.layers.freefloat_archive import ArchiveStore

    return ArchiveStore(db)


def test_lookup_prefers_archive(monkeypatch, tmp_path: Path) -> None:
    # Seed the archive, then make any live download explode. A local-first lookup
    # must read the archive and never touch the network.
    store = _point_archive_at(monkeypatch, tmp_path)
    store.sync_records(ff._parse_xlsx(_make_xlsx(_SAMPLE_ROWS)))

    def boom(_report_date: date) -> tuple[ff.FloatRecord, ...]:
        raise AssertionError("live download must not run when archive has the row")

    monkeypatch.setattr(ff, "_download_report", boom)

    found = ff.lookup("BIST:THYAO")
    assert found is not None
    assert found.code == "THYAO"
    assert found.ratio == pytest.approx(50.43)


def test_lookup_writes_through_on_miss(monkeypatch, tmp_path: Path) -> None:
    store = _point_archive_at(monkeypatch, tmp_path)
    # Live path returns the sample report without touching cache/ratelimit/network.
    monkeypatch.setattr(
        ff, "fetch_report", lambda *a, **k: ff._parse_xlsx(_make_xlsx(_SAMPLE_ROWS))
    )

    assert store.archive_stats()["reports"] == 0
    found = ff.lookup("THYAO")
    assert found is not None and found.code == "THYAO"
    # The miss fetched live and wrote through to the archive.
    assert store.archive_stats()["reports"] == 1
    assert store.read_snapshot("THYAO") is not None


def test_build_float_payload_single_and_all() -> None:
    records = ff._parse_xlsx(_make_xlsx(_SAMPLE_ROWS))
    single = ff.build_float_payload(records, single=records[0])
    assert single["code"] == "THYAO"
    assert single["source"] == "VAP / MKK"
    table = ff.build_float_payload(records)
    assert table["count"] == 2
    assert len(table["companies"]) == 2
