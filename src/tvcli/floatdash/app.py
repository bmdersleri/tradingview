# ruff: noqa: E501
"""Interactive free-float dashboard web server application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse

from ..layers import freefloat_archive
from ..logging_utils import setup_logger
from .routers import alerts, images, market, settings, symbol

logger = setup_logger("tvcli.floatdash")


def create_app(store: freefloat_archive.ArchiveStore | None = None) -> FastAPI:
    app = FastAPI(title="tvcli float-dashboard")
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    logger.info("Initializing tvcli float-dashboard FastAPI app")

    if store is None:
        store = freefloat_archive.ArchiveStore()

    app.state.store = store

    # Reset any sync tasks that were left in "running" state because of server restart
    try:
        with store._connect() as conn:
            rowcount = conn.execute(
                "UPDATE sync_state SET last_status = ?, last_error = ? WHERE last_status = ?",
                ("failed", "Server restarted while sync was running", "running"),
            ).rowcount
            if rowcount > 0:
                logger.info(
                    "Reset sync tasks that were left running", extra={"count": rowcount}
                )
    except Exception:
        logger.exception("Failed to reset stuck sync tasks during app startup")

    # Include modular sub-routers
    app.include_router(settings.router)
    app.include_router(alerts.router)
    app.include_router(market.router)
    app.include_router(symbol.router)
    app.include_router(images.router)

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard_page() -> str:
        template_path = Path(__file__).parent / "templates" / "index.html"
        return template_path.read_text(encoding="utf-8")

    return app
