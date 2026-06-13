# ruff: noqa: E501
"""Interactive free-float dashboard web server application."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from ..layers import freefloat_archive
from ..layers.float_dashboard import DashboardRequest, run_dashboard
from ..logging_utils import setup_logger

logger = setup_logger("tvcli.floatdash")


class SettingsUpdate(BaseModel):
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    low_float_threshold: float = Field(20.0, ge=0.0, le=100.0)
    severe_low_float_threshold: float = Field(10.0, ge=0.0, le=100.0)
    ratio_jump_threshold: float = Field(5.0, ge=0.0, le=100.0)


class SettingsTest(BaseModel):
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None


def create_app(store: freefloat_archive.ArchiveStore | None = None) -> FastAPI:
    app = FastAPI(title="tvcli float-dashboard")
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    logger.info("Initializing tvcli float-dashboard FastAPI app")

    if store is None:
        store = freefloat_archive.ArchiveStore()

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

    tmp_dir = tempfile.mkdtemp(prefix="tvcli_dash_")

    def cleanup() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    atexit.register(cleanup)

    # Simple LRU cache for PNG files
    cache: dict[str, Path] = {}

    def get_latest_report_date() -> str | None:
        stats = store.archive_stats()
        return stats.get("last_report_date")

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard_page() -> str:
        template_path = Path(__file__).parent / "templates" / "index.html"
        return template_path.read_text(encoding="utf-8")

    @app.get("/api/market")
    async def get_api_market() -> Any:
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No archived free-float reports found.",
            )

        try:
            with store._connect() as conn:  # noqa: SLF001
                rows = conn.execute(
                    "SELECT code, name, ratio FROM freefloat_snapshots"
                    " WHERE report_date = ? ORDER BY ratio ASC",
                    (latest_date,),
                ).fetchall()
                event_rows = conn.execute(
                    """
                    SELECT event_type, COUNT(*) AS cnt FROM freefloat_events
                    WHERE report_date = ? AND severity = 'high'
                    GROUP BY event_type ORDER BY cnt DESC
                    """,
                    (latest_date,),
                ).fetchall()

                # Calculate severe risk count (<10%) and warning risk count (10%-20%)
                severe_risk_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio < 10.0",
                    (latest_date,),
                ).fetchone()
                severe_risk_count = (
                    int(severe_risk_row["cnt"]) if severe_risk_row else 0
                )

                warning_risk_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio >= 10.0 AND ratio < 20.0",
                    (latest_date,),
                ).fetchone()
                warning_risk_count = (
                    int(warning_risk_row["cnt"]) if warning_risk_row else 0
                )

                total_high_alerts_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_events WHERE report_date = ? AND severity = 'high'",
                    (latest_date,),
                ).fetchone()
                total_high_alerts = (
                    int(total_high_alerts_row["cnt"]) if total_high_alerts_row else 0
                )

                # Determine previous report date to find top gainers and top losers
                prev_date_row = conn.execute(
                    "SELECT report_date FROM freefloat_reports WHERE report_date < ? ORDER BY report_date DESC LIMIT 1",
                    (latest_date,),
                ).fetchone()
                previous_date = prev_date_row["report_date"] if prev_date_row else None

                top_gainers = []
                top_losers = []
                if previous_date:
                    gainers_rows = conn.execute(
                        """
                        SELECT curr.code, curr.name, curr.ratio AS current_ratio, prev.ratio AS previous_ratio,
                               (curr.ratio - prev.ratio) AS delta
                        FROM freefloat_snapshots curr
                        JOIN freefloat_snapshots prev ON curr.code = prev.code AND prev.report_date = ?
                        WHERE curr.report_date = ? AND delta > 0
                        ORDER BY delta DESC LIMIT 5
                        """,
                        (previous_date, latest_date),
                    ).fetchall()
                    top_gainers = [
                        {
                            "code": r["code"],
                            "name": r["name"],
                            "current_ratio": float(r["current_ratio"]),
                            "previous_ratio": float(r["previous_ratio"]),
                            "delta": float(r["delta"]),
                        }
                        for r in gainers_rows
                    ]

                    losers_rows = conn.execute(
                        """
                        SELECT curr.code, curr.name, curr.ratio AS current_ratio, prev.ratio AS previous_ratio,
                               (curr.ratio - prev.ratio) AS delta
                        FROM freefloat_snapshots curr
                        JOIN freefloat_snapshots prev ON curr.code = prev.code AND prev.report_date = ?
                        WHERE curr.report_date = ? AND delta < 0
                        ORDER BY delta ASC LIMIT 5
                        """,
                        (previous_date, latest_date),
                    ).fetchall()
                    top_losers = [
                        {
                            "code": r["code"],
                            "name": r["name"],
                            "current_ratio": float(r["current_ratio"]),
                            "previous_ratio": float(r["previous_ratio"]),
                            "delta": float(r["delta"]),
                        }
                        for r in losers_rows
                    ]

            all_ratios = [float(r["ratio"]) for r in rows]
            n_symbols = len(all_ratios)
            if n_symbols == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No snapshot data for {latest_date}.",
                )

            import statistics

            median_ratio = statistics.median(all_ratios)

            leaderboard = [
                {"code": r["code"], "name": r["name"], "ratio": float(r["ratio"])}
                for r in rows
            ]
            event_summary = [
                {"event_type": r["event_type"], "count": int(r["cnt"])}
                for r in event_rows
            ]

            # Fetch latest market events representing changes
            all_events = store.symbol_events(limit=100)
            change_event_types = {
                "ratio_jump_up",
                "ratio_jump_down",
                "ratio_threshold_cross_down",
                "ratio_threshold_cross_up",
                "float_shares_jump_up",
                "float_shares_jump_down",
                "capital_change_detected",
            }
            dramatic_changes = [
                e for e in all_events if e["event_type"] in change_event_types
            ]

            return {
                "report_date": latest_date,
                "n_symbols": n_symbols,
                "median_ratio": round(median_ratio, 2),
                "leaderboard": leaderboard,
                "event_summary": event_summary,
                "dramatic_changes": dramatic_changes[:30],  # Limit to top 30
                "summary": {
                    "severe_risk_count": severe_risk_count,
                    "warning_risk_count": warning_risk_count,
                    "total_high_alerts": total_high_alerts,
                },
                "top_gainers": top_gainers,
                "top_losers": top_losers,
            }
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/symbol/{code}")
    async def get_api_symbol(code: str) -> Any:
        try:
            report = store.build_symbol_report(code.upper(), limit=1000)
            return report
        except Exception as e:
            from ..errors import NotFoundError

            if isinstance(e, NotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(e),
                ) from e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/market/sectors")
    async def get_api_market_sectors() -> Any:
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No archived free-float reports found.",
            )
        try:
            return store.get_sector_heatmap(latest_date)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/sync/status")
    async def get_api_sync_status() -> Any:
        try:
            stats = store.archive_stats()
            sync_states = stats.get("sync_state", [])
            vap_state = next((s for s in sync_states if s["source"] == "vap"), None)

            if not vap_state:
                vap_state = {
                    "source": "vap",
                    "last_attempt_at": None,
                    "last_success_at": None,
                    "last_report_date": None,
                    "cooldown_until": None,
                    "last_status": "never_run",
                    "last_error": None,
                }

            cooldown_active = False
            cooldown_seconds_left = 0
            if vap_state.get("cooldown_until"):
                try:
                    cooldown_time = datetime.fromisoformat(vap_state["cooldown_until"])
                    now = datetime.now(UTC)
                    if cooldown_time > now:
                        cooldown_active = True
                        cooldown_seconds_left = int(
                            (cooldown_time - now).total_seconds()
                        )
                except Exception:
                    pass

            return {
                "sync_state": vap_state,
                "cooldown_active": cooldown_active,
                "cooldown_seconds_left": max(0, cooldown_seconds_left),
                "last_report_date": stats.get("last_report_date"),
                "reports_count": stats.get("reports"),
                "symbols_count": stats.get("symbols"),
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    def _bg_sync() -> None:
        try:
            from ..layers import freefloat_archive

            logger.info("Background VAP free-float sync thread started")
            freefloat_archive.sync_archive(
                latest=True,
                since=None,
                until=None,
                max_days=None,
                resume=False,
                store=store,
            )
            logger.info("Background VAP free-float sync thread completed")
        except Exception:
            logger.exception("Background VAP free-float sync thread failed")

    @app.post("/api/sync/run")
    async def run_api_sync(background_tasks: BackgroundTasks) -> Any:
        try:
            logger.info("Starting VAP free-float sync via API")
            now = datetime.now(UTC)
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT cooldown_until FROM sync_state WHERE source = ?",
                    ("vap",),
                ).fetchone()
                if row and row["cooldown_until"]:
                    cooldown_time = datetime.fromisoformat(row["cooldown_until"])
                    if cooldown_time > now:
                        seconds_left = int((cooldown_time - now).total_seconds())
                        logger.warning(
                            "Sync request rejected due to active cooldown",
                            extra={"seconds_left": seconds_left},
                        )
                        return {
                            "success": False,
                            "message": f"Sync cooldown active. Try again in {seconds_left} seconds.",
                            "cooldown_active": True,
                            "cooldown_seconds_left": seconds_left,
                        }

            background_tasks.add_task(_bg_sync)
            logger.info("Sync task queued successfully in background")
            return {"success": True, "message": "Sync started in background."}
        except Exception as e:
            logger.exception("Failed to start sync via API")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/img/market.png")
    async def get_img_market(request: Request) -> Any:
        latest_date = get_latest_report_date()
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
            return FileResponse(
                cache[cache_key], media_type="image/png", headers=headers
            )

        out_path = Path(tmp_dir) / f"{cache_key}.png"
        try:
            logger.info(
                "Generating market image dashboard", extra={"cache_key": cache_key}
            )
            req = DashboardRequest(out=out_path, market=True)
            run_dashboard(req, store=store)
            cache[cache_key] = out_path
            return FileResponse(out_path, media_type="image/png", headers=headers)
        except Exception as e:
            from ..errors import NotFoundError

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

    @app.get("/img/symbol/{code}.png")
    async def get_img_symbol(code: str, request: Request) -> Any:
        symbol = code.upper()
        latest_date = get_latest_report_date()
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
            return FileResponse(
                cache[cache_key], media_type="image/png", headers=headers
            )

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
            from ..errors import NotFoundError

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

    import json

    @app.get("/api/settings")
    async def get_settings() -> Any:
        try:
            from ..config import load_config, resolve_setting

            cfg = load_config()
            token = resolve_setting("alerts", "telegram-token", cfg, "")
            chat_id = resolve_setting("alerts", "telegram-chat-id", cfg, "")
            webhook_url = resolve_setting("alerts", "webhook-url", cfg, "")
            low_float = resolve_setting("alerts", "low-float-threshold", cfg, 20.0)
            severe_low_float = resolve_setting(
                "alerts", "severe-low-float-threshold", cfg, 10.0
            )
            ratio_jump = resolve_setting("alerts", "ratio-jump-threshold", cfg, 5.0)

            masked_token = ""
            if token:
                if len(token) > 8:
                    masked_token = f"{token[:4]}****************{token[-4:]}"
                else:
                    masked_token = "****************"

            return {
                "telegram_token": masked_token,
                "telegram_chat_id": chat_id,
                "webhook_url": webhook_url,
                "low_float_threshold": low_float,
                "severe_low_float_threshold": severe_low_float,
                "ratio_jump_threshold": ratio_jump,
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.post("/api/settings/update")
    async def update_settings(payload: SettingsUpdate) -> Any:
        try:
            from ..config import load_config, resolve_setting, save_config

            cfg = load_config()

            if "alerts" not in cfg:
                cfg["alerts"] = {}

            token = payload.telegram_token
            if token and "*" in token:
                token = resolve_setting("alerts", "telegram-token", cfg, "")

            cfg["alerts"]["telegram-token"] = token or ""
            cfg["alerts"]["telegram-chat-id"] = payload.telegram_chat_id or ""
            cfg["alerts"]["webhook-url"] = payload.webhook_url or ""
            cfg["alerts"]["low-float-threshold"] = payload.low_float_threshold
            cfg["alerts"]["severe-low-float-threshold"] = (
                payload.severe_low_float_threshold
            )
            cfg["alerts"]["ratio-jump-threshold"] = payload.ratio_jump_threshold

            save_config(cfg)
            return {"status": "success", "message": "Settings updated successfully."}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.post("/api/settings/test")
    async def test_settings(payload: SettingsTest) -> Any:
        import httpx

        from ..config import load_config, resolve_setting

        errors = []

        token = payload.telegram_token
        if token and "*" in token:
            cfg = load_config()
            token = resolve_setting("alerts", "telegram-token", cfg, "")

        chat_id = payload.telegram_chat_id
        webhook_url = payload.webhook_url

        if token and chat_id:
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                text = "⚡ <b>tvcli Test Alarmı:</b> Telegram bildirim kanalı bağlantısı başarıyla doğrulandı! ✅"
                res = httpx.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=5.0,
                )
                if res.status_code != 200:
                    errors.append(
                        f"Telegram API returned status {res.status_code}: {res.text}"
                    )
            except Exception as e:
                errors.append(f"Telegram connection error: {str(e)}")

        if webhook_url:
            try:
                test_payload = {
                    "report_date": "TEST_DATE",
                    "events": [
                        {
                            "code": "TEST",
                            "event_type": "test_alert",
                            "severity": "info",
                            "metric_value": 0.0,
                            "threshold_value": 0.0,
                            "payload": {
                                "message": "tvcli Test Alarmı: Webhook bağlantısı başarıyla doğrulandı! ✅"
                            },
                        }
                    ],
                }
                res = httpx.post(webhook_url, json=test_payload, timeout=5.0)
                if res.status_code not in (200, 201, 204):
                    errors.append(
                        f"Webhook returned status {res.status_code}: {res.text}"
                    )
            except Exception as e:
                errors.append(f"Webhook connection error: {str(e)}")

        if not token and not chat_id and not webhook_url:
            return {
                "status": "error",
                "message": "No alert channel configured to test.",
            }

        if errors:
            return {"status": "error", "message": "; ".join(errors)}

        return {"status": "success", "message": "Test notification sent successfully."}

    @app.get("/api/alerts/history")
    async def get_alerts_history(
        symbol: str | None = None,
        severity: str | None = None,
        status_filter: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> Any:
        try:
            query = """
                SELECT id, report_date, code, event_type, severity, metric_value, threshold_value, payload_json, status
                FROM freefloat_events
                WHERE 1=1
            """
            params: list[Any] = []
            if symbol:
                query += " AND code = ?"
                params.append(symbol.upper())
            if severity and severity != "all":
                query += " AND severity = ?"
                params.append(severity.lower())
            if status_filter and status_filter != "all":
                query += " AND status = ?"
                params.append(status_filter.lower())
            if event_type and event_type != "all":
                query += " AND event_type = ?"
                params.append(event_type)

            query += " ORDER BY report_date DESC, id DESC LIMIT ?"
            params.append(limit)

            with store._connect() as conn:
                rows = conn.execute(query, params).fetchall()

            results = []
            for row in rows:
                results.append(
                    {
                        "id": row["id"],
                        "report_date": row["report_date"],
                        "code": row["code"],
                        "event_type": row["event_type"],
                        "severity": row["severity"],
                        "metric_value": row["metric_value"],
                        "threshold_value": row["threshold_value"],
                        "payload": json.loads(row["payload_json"]),
                        "status": row["status"]
                        if row["status"] is not None
                        else "sent",
                    }
                )
            return results
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/symbol/{code}/kap")
    async def get_symbol_kap(code: str) -> Any:
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

    return app
