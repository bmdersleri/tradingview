# ruff: noqa: B008, E501
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ...layers.freefloat_archive import ArchiveStore
from ..dependencies import get_store

router = APIRouter(prefix="/api/symbol", tags=["symbol"])


@router.get("/{code}")
async def get_api_symbol(code: str, store: ArchiveStore = Depends(get_store)) -> Any:
    try:
        report = store.build_symbol_report(code.upper(), limit=1000)
        return report
    except Exception as e:
        from ...errors import NotFoundError

        if isinstance(e, NotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@router.get("/{code}/kap")
async def get_symbol_kap(code: str, store: ArchiveStore = Depends(get_store)) -> Any:
    try:
        query = """
            SELECT id, code, disclosure_date, title, summary, url
            FROM kap_disclosures
            WHERE code = ?
            ORDER BY disclosure_date DESC
        """
        with store._connect() as conn:
            rows = conn.execute(query, (code.upper(),)).fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "id": row["id"],
                    "code": row["code"],
                    "disclosure_date": row["disclosure_date"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                }
            )
        return results
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@router.get("/{code}/ohlcv")
async def get_symbol_ohlcv(
    code: str,
    interval: str = "1d",
    bars: int = 250,
) -> Any:
    try:
        from ...layers import ohlcv
        from ...logging_utils import setup_logger

        symbol_code = code.upper()
        if ":" not in symbol_code:
            symbol_code = f"BIST:{symbol_code}"

        req = ohlcv.OhlcvRequest(symbol=symbol_code, interval=interval, bars=bars)
        bars_data = ohlcv.fetch_history(req)

        return [
            {
                "time": bar.time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars_data
        ]
    except Exception as e:
        from ...logging_utils import setup_logger

        logger = setup_logger("tvcli.floatdash.symbol")
        logger.warning(f"Failed to fetch OHLCV for {code}: {str(e)}")
        return []
