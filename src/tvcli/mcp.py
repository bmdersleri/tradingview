"""MCP wrapper over the tvcli core functions."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, cast

from .errors import UsageError
from .layers import chart, ohlcv, screener, ta

P = ParamSpec("P")
R = TypeVar("R")

FastMCP: Any | None = None


def _load_fastmcp() -> Any | None:
    global FastMCP
    if FastMCP is not None:
        return FastMCP
    try:
        module = importlib.import_module("mcp.server.fastmcp")
    except ModuleNotFoundError:
        return None
    FastMCP = getattr(module, "FastMCP", None)
    return FastMCP


def _require_fastmcp() -> Any:
    fastmcp = _load_fastmcp()
    if fastmcp is None:
        raise UsageError(
            "MCP support requires the optional `mcp` dependency.",
            hint="Install tvcli with the `mcp` extra and retry.",
        )
    return fastmcp


def _tool_decorator(server: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    return cast(Callable[[Callable[P, R]], Callable[P, R]], server.tool())


def _split_csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def register_tools(server: Any) -> None:
    tool = _tool_decorator(server)

    @tool
    def data_screen(
        market: str,
        select: str,
        where: str | None = None,
        order_by: str | None = None,
        desc: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        request = screener.ScreenRequest(
            market=market,
            select=screener.split_select(select),
            where=screener.parse_where(where),
            order_by=order_by,
            descending=desc,
            limit=limit,
        )
        return screener.build_screen_payload(
            request,
            screener.run_screen_query(request),
        )

    @tool
    def data_fields(market: str, search: str | None = None) -> dict[str, Any]:
        return screener.build_fields_payload(
            market,
            screener.run_fields_query(market, search),
        )

    @tool
    def data_search(query: str, market: str | None = None) -> dict[str, Any]:
        return screener.build_search_payload(
            query,
            market,
            screener.run_search_query(query, market),
        )

    @tool
    def data_quote(symbols: str) -> dict[str, Any]:
        symbol_list = _split_csv(symbols)
        return screener.build_quote_payload(
            symbol_list,
            screener.run_quote_query(symbol_list),
        )

    @tool
    def ta_get(
        symbol: str,
        interval: str = "1d",
        screener_name: str = "auto",
    ) -> dict[str, Any]:
        request = ta.TaRequest(
            symbol=symbol,
            interval=interval,
            screener=ta.derive_screener(symbol, screener_name),
        )
        return ta.build_snapshot_payload(request, ta.run_ta_get(request))

    @tool
    def ta_matrix(symbol: str, intervals: str = "1h,4h,1d") -> dict[str, Any]:
        interval_list = _split_csv(intervals)
        return ta.build_matrix_payload(
            symbol,
            interval_list,
            tuple(
                ta.build_snapshot_payload(
                    ta.TaRequest(
                        symbol=symbol,
                        interval=interval,
                        screener=ta.derive_screener(symbol),
                    ),
                    ta.run_ta_get(
                        ta.TaRequest(
                            symbol=symbol,
                            interval=interval,
                            screener=ta.derive_screener(symbol),
                        )
                    ),
                )
                for interval in interval_list
            ),
        )

    @tool
    def ohlcv_get(
        symbol: str,
        interval: str = "1d",
        bars: int = 500,
    ) -> dict[str, Any]:
        request = ohlcv.OhlcvRequest(symbol=symbol, interval=interval, bars=bars)
        return ohlcv.build_ohlcv_payload(request, ohlcv.fetch_history(request))

    @tool
    def chart_shot(
        symbol: str,
        out: str,
        interval: str = "1d",
        studies: str | None = None,
        theme: str = "dark",
        width: int = 1600,
        height: int = 900,
    ) -> dict[str, Any]:
        request = chart.ChartRequest(
            symbol=symbol,
            interval=interval,
            out=Path(out),
            width=width,
            height=height,
            theme=theme,
            studies=_split_csv(studies),
        )
        return chart.shot_chart(request)


def build_server() -> Any:
    server_cls = _require_fastmcp()
    server = server_cls("tvcli")
    register_tools(server)
    return server


def run_mcp_server() -> None:
    server = build_server()
    runner = getattr(server, "run", None)
    if runner is None:
        raise UsageError(
            "The MCP server object does not provide a run method.",
            hint="Update the optional MCP dependency and retry.",
        )
    runner()
