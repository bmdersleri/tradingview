# ruff: noqa: B008, E501
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from ...layers.float_dashboard import DashboardRequest, run_dashboard
from ...layers.freefloat_archive import ArchiveStore
from ...logging_utils import setup_logger
from ..dependencies import get_store

logger = setup_logger("tvcli.floatdash.images")

router = APIRouter(prefix="/img", tags=["images"])

# Create temporary directory for generated images
tmp_dir = tempfile.mkdtemp(prefix="tvcli_dash_")
cache: dict[str, Path] = {}


def cleanup() -> None:
    shutil.rmtree(tmp_dir, ignore_errors=True)


atexit.register(cleanup)


def get_latest_report_date(store: ArchiveStore) -> str | None:
    stats = store.archive_stats()
    return stats.get("last_report_date")


@router.get("/market.png")
async def get_img_market(
    request: Request, store: ArchiveStore = Depends(get_store)
) -> Any:
    latest_date = get_latest_report_date(store)
    if not latest_date:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No archived free-float reports found.",
        )

    cache_key = f"market_{latest_date}"
    etag = f'"{cache_key}"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match == etag:
        logger.info(
            "Serving market image via 304 Not Modified",
            extra={"cache_key": cache_key},
        )
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=3600",
    }

    if cache_key in cache and cache[cache_key].exists():
        logger.info(
            "Serving market image from memory cache", extra={"cache_key": cache_key}
        )
        return FileResponse(cache[cache_key], media_type="image/png", headers=headers)

    out_path = Path(tmp_dir) / f"{cache_key}.png"
    try:
        logger.info("Generating market image dashboard", extra={"cache_key": cache_key})
        req = DashboardRequest(out=out_path, market=True)
        run_dashboard(req, store=store)
        cache[cache_key] = out_path
        return FileResponse(out_path, media_type="image/png", headers=headers)
    except Exception as e:
        from ...errors import NotFoundError

        logger.exception(
            "Failed to generate market image dashboard",
            extra={"cache_key": cache_key},
        )
        if isinstance(e, NotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@router.get("/symbol/{code}.png")
async def get_img_symbol(
    code: str, request: Request, store: ArchiveStore = Depends(get_store)
) -> Any:
    symbol = code.upper()
    latest_date = get_latest_report_date(store)
    if not latest_date:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No reports found.",
        )

    cache_key = f"symbol_{symbol}_{latest_date}"
    etag = f'"{cache_key}"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match == etag:
        logger.info(
            "Serving symbol image via 304 Not Modified",
            extra={"symbol": symbol, "cache_key": cache_key},
        )
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=3600",
    }

    if cache_key in cache and cache[cache_key].exists():
        logger.info(
            "Serving symbol image from memory cache",
            extra={"symbol": symbol, "cache_key": cache_key},
        )
        return FileResponse(cache[cache_key], media_type="image/png", headers=headers)

    out_path = Path(tmp_dir) / f"{cache_key}.png"
    try:
        logger.info(
            "Generating symbol image dashboard",
            extra={"symbol": symbol, "cache_key": cache_key},
        )
        req = DashboardRequest(out=out_path, symbol=symbol)
        run_dashboard(req, store=store)
        cache[cache_key] = out_path
        return FileResponse(out_path, media_type="image/png", headers=headers)
    except Exception as e:
        from ...errors import NotFoundError

        logger.exception(
            "Failed to generate symbol image dashboard",
            extra={"symbol": symbol, "cache_key": cache_key},
        )
        if isinstance(e, NotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
