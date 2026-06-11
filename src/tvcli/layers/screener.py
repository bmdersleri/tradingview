"""Pure screener query parsing and payload shaping."""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from ..errors import TvcliError, UsageError

CLAUSE_PATTERN = re.compile(
    r"^\s*(?P<field>[A-Za-z_][\w.]*)\s*"
    r"(?P<operator>>=|<=|==|!=|>|<|in|between)\s*"
    r"(?P<value>.+?)\s*$"
)


@dataclass(frozen=True, slots=True)
class WhereClause:
    field: str
    operator: str
    value: Any


@dataclass(frozen=True, slots=True)
class ScreenRequest:
    market: str
    select: tuple[str, ...]
    where: tuple[WhereClause, ...]
    order_by: str | None = None
    descending: bool = False
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ScreenResult:
    rows: tuple[dict[str, Any], ...]
    total_matches: int | None = None


@dataclass(frozen=True, slots=True)
class FieldInfo:
    name: str
    type: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ScreenerBackend:
    Query: type[Any]
    Column: type[Any]
    Or: Any
    And: Any
    pd: Any


def split_select(select: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in select.split(",") if part.strip())


def _load_backend() -> ScreenerBackend:
    try:
        module = importlib.import_module("tradingview_screener")
        pd = importlib.import_module("pandas")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise TvcliError(
            "TradingView screener backend is unavailable.",
            hint="Install the `market` extra to enable data screen/search/quote.",
        ) from exc
    return ScreenerBackend(
        Query=module.Query,
        Column=module.Column,
        Or=module.Or,
        And=module.And,
        pd=pd,
    )


def _parse_scalar(value: str) -> Any:
    candidate = value.strip()
    try:
        return ast.literal_eval(candidate)
    except (SyntaxError, ValueError):
        if re.fullmatch(r"-?\d+", candidate):
            return int(candidate)
        if re.fullmatch(r"-?\d+\.\d+", candidate):
            return float(candidate)
        return candidate


def _parse_sequence(value: str) -> tuple[Any, ...]:
    candidate = value.strip().strip("[]()")
    if not candidate:
        raise ValueError("empty sequence")
    return tuple(_parse_scalar(part) for part in candidate.split(",") if part.strip())


def parse_where(expression: str | None) -> tuple[WhereClause, ...]:
    if not expression:
        return ()
    clauses: list[WhereClause] = []
    for raw_clause in (part.strip() for part in expression.split(";") if part.strip()):
        match = CLAUSE_PATTERN.match(raw_clause)
        if match is None:
            raise UsageError(
                f"Invalid filter clause: {raw_clause}",
                hint=f"Offending clause: {raw_clause}",
            )
        operator = match.group("operator")
        raw_value = match.group("value")
        try:
            if operator == "in":
                value: Any = _parse_sequence(raw_value)
            elif operator == "between":
                sequence = _parse_sequence(raw_value)
                if len(sequence) != 2:
                    raise ValueError("between requires exactly two values")
                value = sequence
            else:
                value = _parse_scalar(raw_value)
        except ValueError as exc:
            raise UsageError(
                f"Invalid filter clause: {raw_clause}",
                hint=f"Offending clause: {raw_clause}",
            ) from exc
        clauses.append(
            WhereClause(
                field=match.group("field"),
                operator=operator,
                value=value,
            )
        )
    return tuple(clauses)


def _normalize_ticker(row: dict[str, Any]) -> str | None:
    ticker = row.get("ticker")
    if ticker is not None:
        text = str(ticker).strip()
        if text and text.lower() not in {"nan", "<na>"}:
            return text
    exchange = row.get("exchange")
    symbol = row.get("symbol") or row.get("name")
    if exchange and symbol:
        return f"{exchange}:{symbol}"
    return None


@lru_cache(maxsize=1)
def _field_catalog() -> tuple[FieldInfo, ...]:
    fields = [
        ("name", "text", "Display name"),
        ("ticker", "text", "Exchange:symbol identifier"),
        ("exchange", "text", "Exchange code"),
        ("description", "text", "Security description"),
        ("type", "text", "Instrument type"),
        ("typespecs", "set", "Instrument type tags"),
        ("market", "text", "TradingView market"),
        ("country", "text", "Country"),
        ("currency", "text", "Currency code"),
        ("close", "number", "Last price"),
        ("open", "number", "Open price"),
        ("high", "number", "High price"),
        ("low", "number", "Low price"),
        ("volume", "number", "Volume"),
        ("change", "number", "Price change"),
        ("relative_volume_10d_calc", "number", "Relative volume"),
        ("market_cap_basic", "number", "Market cap"),
        ("price_earnings_ttm", "number", "Trailing P/E"),
        ("earnings_per_share_diluted_ttm", "number", "EPS diluted TTM"),
        ("dividends_yield_current", "number", "Dividend yield"),
        ("RSI", "number", "Relative strength index"),
        ("MACD.macd", "number", "MACD value"),
        ("MACD.signal", "number", "MACD signal"),
        ("VWAP", "number", "Volume weighted average price"),
        ("EMA5", "number", "5-period EMA"),
        ("EMA20", "number", "20-period EMA"),
        ("price_52_week_high", "number", "52-week high"),
        ("price_52_week_low", "number", "52-week low"),
        ("AnalystRating", "text", "Analyst rating"),
        ("AnalystRating.tr", "text", "Analyst rating translation"),
        ("sector", "text", "Sector"),
        ("sector.tr", "text", "Sector translation"),
        ("fundamental_currency_code", "text", "Fundamental currency"),
    ]
    return tuple(
        FieldInfo(name=name, type=kind, description=description)
        for name, kind, description in fields
    )


def list_fields(search: str | None = None) -> tuple[FieldInfo, ...]:
    catalog = _field_catalog()
    if not search:
        return catalog
    needle = search.casefold()
    return tuple(
        field
        for field in catalog
        if needle in field.name.casefold()
        or (field.type is not None and needle in field.type.casefold())
        or (field.description is not None and needle in field.description.casefold())
    )


def _parse_expression(backend: ScreenerBackend, clause: WhereClause) -> Any:
    column = backend.Column(clause.field)
    if clause.operator == ">":
        return column > clause.value
    if clause.operator == ">=":
        return column >= clause.value
    if clause.operator == "<":
        return column < clause.value
    if clause.operator == "<=":
        return column <= clause.value
    if clause.operator == "==":
        return column == clause.value
    if clause.operator == "!=":
        return column != clause.value
    if clause.operator == "in":
        return column.isin(clause.value)
    if clause.operator == "between":
        left, right = clause.value
        return column.between(left, right)
    raise UsageError(
        f"Unsupported operator: {clause.operator}",
        hint=f"Offending clause: {clause.field} {clause.operator} {clause.value}",
    )


def _build_dataframe_rows(
    backend: ScreenerBackend,
    df: Any,
) -> tuple[dict[str, Any], ...]:
    normalized = df.where(backend.pd.notna(df), None)
    rows = normalized.to_dict(orient="records")
    return tuple(dict(row) for row in rows)


def run_screen_query(request: ScreenRequest) -> ScreenResult:
    backend = _load_backend()
    query = backend.Query(request.market)
    query.select(*request.select)
    if request.where:
        query.where(*(_parse_expression(backend, clause) for clause in request.where))
    if request.order_by:
        query.order_by(request.order_by, ascending=not request.descending)
    query.limit(request.limit)
    total_count, df = query.get_scanner_data()
    rows = []
    for row in _build_dataframe_rows(backend, df):
        normalized = dict(row)
        ticker = _normalize_ticker(normalized)
        if ticker is not None:
            normalized["ticker"] = ticker
        rows.append(normalized)
    return ScreenResult(
        rows=tuple(rows),
        total_matches=total_count,
    )


def run_fields_query(market: str, search: str | None) -> tuple[FieldInfo, ...]:
    _ = market
    return list_fields(search)


def _looks_like_ticker(query: str) -> bool:
    if ":" not in query:
        return False
    exchange, symbol = query.split(":", 1)
    return bool(exchange and symbol)


def run_search_query(query: str, market: str | None) -> tuple[dict[str, Any], ...]:
    backend = _load_backend()
    screener_market = market or "america"
    tvquery = backend.Query(screener_market)
    tvquery.select("name", "description", "exchange", "type", "market")
    if _looks_like_ticker(query):
        tvquery.set_tickers(query.upper())
    else:
        tvquery.where2(
            backend.Or(
                backend.Column("name").like(query),
                backend.Column("description").like(query),
                backend.Column("exchange").like(query),
                backend.Column("ticker").like(query),
            )
        )
    _, df = tvquery.limit(50).get_scanner_data()
    rows = _build_dataframe_rows(backend, df)
    candidates = []
    for row in rows:
        normalized = dict(row)
        ticker = _normalize_ticker(normalized)
        if ticker is not None:
            normalized["ticker"] = ticker
        candidates.append(normalized)
    return tuple(candidates)


def run_quote_query(symbols: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    backend = _load_backend()
    query = backend.Query()
    query.select("name", "close", "change", "volume", "market_cap_basic")
    query.set_tickers(*symbols)
    _, df = query.get_scanner_data()
    rows = []
    index = {symbol.upper(): position for position, symbol in enumerate(symbols)}
    for row in _build_dataframe_rows(backend, df):
        normalized = dict(row)
        ticker = _normalize_ticker(normalized)
        if ticker is not None:
            normalized["ticker"] = ticker
        rows.append(normalized)
    rows.sort(key=lambda row: index.get(str(row.get("ticker", "")).upper(), len(index)))
    return tuple(rows)


def build_screen_payload(
    request: ScreenRequest,
    result: ScreenResult,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in result.rows:
        normalized = dict(row)
        ticker = _normalize_ticker(normalized)
        if ticker is not None:
            normalized["ticker"] = ticker
        rows.append(normalized)
    return {
        "market": request.market,
        "total_matches": result.total_matches
        if result.total_matches is not None
        else len(rows),
        "returned": len(rows),
        "columns": list(request.select),
        "rows": rows,
    }


def build_fields_payload(market: str, fields: tuple[FieldInfo, ...]) -> dict[str, Any]:
    return {
        "market": market,
        "returned": len(fields),
        "fields": [asdict(field) for field in fields],
    }


def build_search_payload(
    query: str,
    market: str | None,
    candidates: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "query": query,
        "market": market,
        "returned": len(candidates),
        "candidates": [dict(candidate) for candidate in candidates],
    }


def build_quote_payload(
    symbols: tuple[str, ...],
    rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "symbols": list(symbols),
        "returned": len(rows),
        "rows": [dict(row) for row in rows],
    }


def unsupported_backend(name: str) -> TvcliError:
    return TvcliError(
        f"{name} backend is not wired yet.",
        hint="Install the market dependencies and complete the TradingView adapter.",
    )
