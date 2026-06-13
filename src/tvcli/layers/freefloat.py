"""VAP (vap.org.tr / MKK) free-float ("fiili dolaşım") ratios for BIST stocks.

The public report at ``/api/all-companies`` is a two-step HTML form: GET the page
to scrape a hidden ``as_fid`` token (and pick up a session cookie), then POST the
date plus that token to receive an ``.xlsx`` of every listed company. The columns
are: Tarih, ISIN, ISIN Açıklama, Borsa Kodu, İhraççı Üye, Fiili Dolaşımdaki Pay
Adedi, İhraççı Sermaye, Fiili Pay/Sermaye Oranı (%).

The whole daily report comes in one download, so we fetch it once, cache it for a
day, and filter locally — both the single-symbol and the full-table commands share
that one cached payload. A token-bucket throttle keeps us under VAP's "5 reports
per 10 minutes" limit.

This is official public data; use it for personal/research workflows with the
conservative rate limit kept here. Free-float is a static liquidity metric, not a
trading signal.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from ..cache import SQLiteTTLCache
from ..config import default_cache_path
from ..errors import NetworkError, NotFoundError, UpstreamChangedError
from ..ratelimit import SQLiteTokenBucket

_VAP_URL = "https://www.vap.org.tr/api/all-companies"
_USER_AGENT = "Mozilla/5.0 (compatible; tvcli/1.0; +https://github.com/)"
_SOURCE = "VAP / MKK"

# SpreadsheetML namespace used throughout the .xlsx XML parts.
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Expected header row (row 3 in the report). We validate against it so a silent
# upstream column reshuffle surfaces as UpstreamChangedError instead of garbage.
_EXPECTED_HEADER = (
    "Tarih",
    "ISIN",
    "ISIN Açıklama",
    "Borsa Kodu",
    "İhraççı Üye",
    "Fiili Dolaşımdaki Pay Adedi",
    "İhraççı Sermaye",
    "Fiili Pay/Sermaye Oranı (%)",
)

_CACHE_TTL_SECONDS = 24 * 60 * 60
# 5 tokens, refilling one every 120 s => at most 5 in a 10-minute window.
_RATE_CAPACITY = 5.0
_RATE_REFILL_PER_SECOND = 1.0 / 120.0
_RATE_BUCKET = "vap_free_float"


@dataclass(frozen=True, slots=True)
class FloatRecord:
    code: str  # Borsa Kodu, e.g. "THYAO"
    isin: str
    name: str  # ISIN Açıklama
    float_shares: float  # Fiili Dolaşımdaki Pay Adedi
    capital: float  # İhraççı Sermaye
    ratio: float  # Fiili Pay/Sermaye Oranı (%)
    date: str  # Tarih as reported, e.g. "11.06.2026"


def normalize_code(symbol: str) -> str:
    """``"BIST:THYAO"`` / ``"thyao"`` -> ``"THYAO"`` (the VAP Borsa Kodu)."""
    code = symbol.split(":", 1)[1] if ":" in symbol else symbol
    return code.strip().upper()


def is_bist_symbol(symbol: str) -> bool:
    """True when the symbol is (or defaults to) a BIST listing VAP can cover."""
    if ":" not in symbol:
        return True  # bare ticker — assume BIST scope
    return symbol.split(":", 1)[0].strip().upper() == "BIST"


def _format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _cell_value(cell: ET.Element, shared: list[str]) -> str | None:
    v = cell.find(_NS + "v")
    if v is None or v.text is None:
        # Inline string (rare) stored under <is><t>.
        inline = cell.find(_NS + "is")
        if inline is not None:
            return "".join(t.text or "" for t in inline.iter(_NS + "t"))
        return None
    if cell.get("t") == "s":
        return shared[int(v.text)]
    return v.text


def _column_letter(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _to_float(text: str | None) -> float:
    if not text:
        return 0.0
    # Reports use a plain dot decimal; strip thousands separators defensively.
    cleaned = text.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_xlsx(content: bytes) -> tuple[FloatRecord, ...]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = [
            "".join(t.text or "" for t in si.iter(_NS + "t"))
            for si in shared_root.findall(_NS + "si")
        ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise UpstreamChangedError(
            "VAP returned a report that is not a readable .xlsx.",
            hint="The VAP report format may have changed; inspect the adapter.",
        ) from exc

    sheet_data = sheet.find(_NS + "sheetData")
    rows = sheet_data.findall(_NS + "row") if sheet_data is not None else []

    header: dict[str, str] = {}
    records: list[FloatRecord] = []
    for row in rows:
        cells = {
            _column_letter(c.get("r", "")): _cell_value(c, shared)
            for c in row.findall(_NS + "c")
        }
        if not header:
            # The header is the first row whose A column equals "Tarih".
            if cells.get("A") == "Tarih":
                header = {k: (v or "") for k, v in cells.items()}
                ordered = tuple(header.get(col, "") for col in "ABCDEFGH")
                if ordered != _EXPECTED_HEADER:
                    raise UpstreamChangedError(
                        "VAP report columns do not match the expected layout.",
                        hint="Update the freefloat adapter to the new columns.",
                    )
            continue
        code = (cells.get("D") or "").strip()
        if not code:
            continue
        records.append(
            FloatRecord(
                code=code.upper(),
                isin=(cells.get("B") or "").strip(),
                name=(cells.get("C") or "").strip(),
                float_shares=_to_float(cells.get("F")),
                capital=_to_float(cells.get("G")),
                ratio=_to_float(cells.get("H")),
                date=(cells.get("A") or "").strip(),
            )
        )

    if not header:
        raise UpstreamChangedError(
            "VAP report did not contain the expected header row.",
            hint="The VAP report format may have changed; inspect the adapter.",
        )
    return tuple(records)


def _download_report(report_date: date) -> tuple[FloatRecord, ...]:
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            form = client.get(_VAP_URL)
            form.raise_for_status()
            match = re.search(r'name="as_fid"\s+value="([^"]+)"', form.text)
            if match is None:
                raise UpstreamChangedError(
                    "VAP form token (as_fid) was not found.",
                    hint="The VAP form layout may have changed.",
                )
            response = client.post(
                _VAP_URL,
                data={"date": _format_date(report_date), "as_fid": match.group(1)},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NetworkError(
            "Unable to fetch the VAP free-float report.",
            hint="Check connectivity and retry.",
        ) from exc
    # VAP serves an HTML page (not an .xlsx) for dates with no published report
    # yet — today's balances are released the next business day. Treat that as
    # "no data for this date", distinct from a genuine format change.
    if not response.content.startswith(b"PK"):
        raise NotFoundError(
            f"VAP has no free-float report for {_format_date(report_date)}.",
            hint="Try an earlier --date (reports lag one business day).",
        )
    return _parse_xlsx(response.content)


# When no date is given, walk back from today over at most this many days to the
# latest published report (covers weekends/holidays + the one-day publish lag).
_AUTO_LOOKBACK_DAYS = 7


def _fetch_for_date(
    target: date,
    cache: SQLiteTTLCache,
    throttle: SQLiteTokenBucket,
) -> tuple[FloatRecord, ...]:
    key = f"vap_float_{target.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return tuple(FloatRecord(**row) for row in cached)

    if not throttle.allow(_RATE_BUCKET):
        from ..errors import RateLimitedError

        raise RateLimitedError(
            "VAP free-float rate limit reached (5 reports / 10 minutes).",
            hint="Wait a couple of minutes and retry; daily data is cached.",
        )

    records = _download_report(target)
    cache.set(key, [asdict(r) for r in records], _CACHE_TTL_SECONDS)
    return records


def fetch_report(
    report_date: date | None = None,
    *,
    cache: SQLiteTTLCache | None = None,
    throttle: SQLiteTokenBucket | None = None,
) -> tuple[FloatRecord, ...]:
    """Fetch the daily report, cache-first, respecting the VAP rate limit.

    With an explicit ``report_date`` the report for exactly that day is returned
    (or NotFoundError if VAP has none). With ``report_date=None`` we walk back
    from today to the most recent published report — reports lag one business
    day and there are none on weekends/holidays.
    """
    cache = cache or SQLiteTTLCache(default_cache_path())
    throttle = throttle or SQLiteTokenBucket(
        default_cache_path(),
        capacity=_RATE_CAPACITY,
        refill_per_second=_RATE_REFILL_PER_SECOND,
    )

    if report_date is not None:
        return _fetch_for_date(report_date, cache, throttle)

    last_error: Exception | None = None
    for offset in range(1, _AUTO_LOOKBACK_DAYS + 1):
        candidate = _today() - timedelta(days=offset)
        try:
            return _fetch_for_date(candidate, cache, throttle)
        except NotFoundError as exc:
            last_error = exc
            continue
    raise NotFoundError(
        "No VAP free-float report found in the last week.",
        hint="Pass an explicit --date (DD/MM/YYYY) for an older report.",
    ) from last_error


def _archive_lookup(target_code: str, report_date: date | None) -> FloatRecord | None:
    """Read one symbol from the local archive without touching the network.

    Lazy import keeps the freefloat <-> freefloat_archive dependency one-way at
    module load (the archive layer imports this module).
    """
    from .freefloat_archive import ArchiveStore

    try:
        store = ArchiveStore()
        return store.read_snapshot(target_code, report_date)
    except Exception:
        # The archive is an optimization; never let a store error block a lookup.
        return None


def lookup(
    code: str,
    report_date: date | None = None,
    *,
    cache: SQLiteTTLCache | None = None,
    throttle: SQLiteTokenBucket | None = None,
    use_archive: bool = True,
) -> FloatRecord | None:
    """Free-float for one symbol, local-first.

    Reads the persistent archive first; on a miss it fetches the live VAP report
    and writes it through to the archive so the next lookup is offline. Passing
    an explicit ``cache``/``throttle`` (tests) keeps the direct live path.
    """
    target_code = normalize_code(code)

    if use_archive and cache is None and throttle is None:
        archived = _archive_lookup(target_code, report_date)
        if archived is not None:
            return archived
        # Miss: fetch live, write through to the archive, then return.
        records = fetch_report(report_date)
        if records:
            from .freefloat_archive import ArchiveStore

            with suppress(Exception):
                ArchiveStore().sync_records(records)
        return next((r for r in records if r.code == target_code), None)

    for record in fetch_report(report_date, cache=cache, throttle=throttle):
        if record.code == target_code:
            return record
    return None


def build_float_payload(
    records: tuple[FloatRecord, ...],
    *,
    single: FloatRecord | None = None,
    report_date: date | None = None,
) -> dict[str, Any]:
    if single is not None:
        return {
            "code": single.code,
            "isin": single.isin,
            "name": single.name,
            "ratio": single.ratio,
            "float_shares": single.float_shares,
            "capital": single.capital,
            "date": single.date,
            "source": _SOURCE,
        }
    return {
        "date": records[0].date if records else None,
        "count": len(records),
        "companies": [asdict(r) for r in records],
        "source": _SOURCE,
    }
