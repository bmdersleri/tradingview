from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tvcli.layers import screener


@dataclass
class FakeQuery:
    market: str

    def __post_init__(self) -> None:
        self.selected: tuple[str, ...] = ()
        self.where_exprs: tuple[object, ...] = ()
        self.where2_expr: object | None = None
        self.order_by_args: tuple[object, bool] | None = None
        self.limit_value: int | None = None
        self.tickers: tuple[str, ...] = ()

    def select(self, *columns: str) -> FakeQuery:
        self.selected = columns
        return self

    def where(self, *expressions: object) -> FakeQuery:
        self.where_exprs = expressions
        return self

    def where2(self, operation: object) -> FakeQuery:
        self.where2_expr = operation
        return self

    def order_by(self, column: object, ascending: bool = True) -> FakeQuery:
        self.order_by_args = (column, ascending)
        return self

    def limit(self, value: int) -> FakeQuery:
        self.limit_value = value
        return self

    def set_tickers(self, *tickers: str) -> FakeQuery:
        self.tickers = tickers
        return self

    def get_scanner_data(self) -> tuple[int, pd.DataFrame]:
        if self.tickers:
            frame = pd.DataFrame(
                [
                    {
                        "ticker": self.tickers[0],
                        "name": "THYAO",
                        "close": 312.5,
                        "change": 1.2,
                        "volume": 123,
                        "market_cap_basic": 1000,
                    }
                ]
            )
            return 1, frame
        frame = pd.DataFrame(
            [
                {
                    "ticker": "BIST:THYAO",
                    "name": "THYAO",
                    "close": 312.5,
                    "volume": 18234567,
                    "RSI": 27.4,
                    "market_cap_basic": 431000000000,
                },
                {
                    "exchange": "BIST",
                    "symbol": "AKBNK",
                    "name": "AKBNK",
                    "close": 45.1,
                    "volume": 9000000,
                },
            ]
        )
        return 2, frame


class FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name

    def like(self, value: str) -> tuple[str, str, str]:
        return ("like", self.name, value)

    def isin(self, values: tuple[object, ...]) -> tuple[str, str, tuple[object, ...]]:
        return ("in", self.name, values)

    def between(self, left: object, right: object) -> tuple[str, str, object, object]:
        return ("between", self.name, left, right)

    def __gt__(self, other: object) -> tuple[str, str, object]:
        return (">", self.name, other)

    def __ge__(self, other: object) -> tuple[str, str, object]:
        return (">=", self.name, other)

    def __lt__(self, other: object) -> tuple[str, str, object]:
        return ("<", self.name, other)

    def __le__(self, other: object) -> tuple[str, str, object]:
        return ("<=", self.name, other)

    def __eq__(self, other: object) -> tuple[str, str, object]:  # type: ignore[override]
        return ("==", self.name, other)

    def __ne__(self, other: object) -> tuple[str, str, object]:  # type: ignore[override]
        return ("!=", self.name, other)


def test_run_screen_query_builds_expected_request(monkeypatch) -> None:
    queries: list[FakeQuery] = []

    def fake_query(market: str = "america") -> FakeQuery:
        query = FakeQuery(market)
        queries.append(query)
        return query

    monkeypatch.setattr(
        screener,
        "_load_backend",
        lambda: screener.ScreenerBackend(
            Query=fake_query,
            Column=FakeColumn,
            Or=lambda *args: ("or", args),
            And=lambda *args: ("and", args),
            pd=pd,
        ),
    )

    result = screener.run_screen_query(
        screener.ScreenRequest(
            market="turkey",
            select=("name", "close", "volume", "RSI", "market_cap_basic"),
            where=screener.parse_where("RSI<30;volume>1000000"),
            order_by="volume",
            descending=True,
            limit=20,
        )
    )

    assert result.total_matches == 2
    assert result.rows[1]["ticker"] == "BIST:AKBNK"
    assert queries[0].selected == ("name", "close", "volume", "RSI", "market_cap_basic")
    assert queries[0].order_by_args == ("volume", False)
    assert queries[0].limit_value == 20


def test_run_search_and_quote_queries(monkeypatch) -> None:
    calls: list[FakeQuery] = []

    def fake_query(market: str = "america") -> FakeQuery:
        query = FakeQuery(market)
        calls.append(query)
        return query

    monkeypatch.setattr(
        screener,
        "_load_backend",
        lambda: screener.ScreenerBackend(
            Query=fake_query,
            Column=FakeColumn,
            Or=lambda *args: ("or", args),
            And=lambda *args: ("and", args),
            pd=pd,
        ),
    )

    search_rows = screener.run_search_query("THYAO", "turkey")
    quote_rows = screener.run_quote_query(("BIST:THYAO",))

    assert search_rows[0]["ticker"] == "BIST:THYAO"
    assert quote_rows[0]["ticker"] == "BIST:THYAO"
    assert calls[0].where2_expr[0] == "or"
    assert calls[1].tickers == ("BIST:THYAO",)


def test_field_catalog_search() -> None:
    fields = screener.list_fields("rsi")

    assert any(field.name == "RSI" for field in fields)
