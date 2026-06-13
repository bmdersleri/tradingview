# ruff: noqa: B008, E501
from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from ...layers.freefloat_archive import ArchiveStore
from ...logging_utils import setup_logger
from ..dependencies import ConnectionManager, get_store, get_ws_manager

logger = setup_logger("tvcli.floatdash.market")

router = APIRouter(tags=["market"])


class SyncRequest(BaseModel):
    latest: bool = True
    since: str | None = None
    until: str | None = None


def get_latest_report_date(store: ArchiveStore) -> str | None:
    stats = store.archive_stats()
    return stats.get("last_report_date")


@router.get("/api/market")
async def get_api_market(store: ArchiveStore = Depends(get_store)) -> Any:
    latest_date = get_latest_report_date(store)
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
            severe_risk_count = int(severe_risk_row["cnt"]) if severe_risk_row else 0

            warning_risk_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio >= 10.0 AND ratio < 20.0",
                (latest_date,),
            ).fetchone()
            warning_risk_count = int(warning_risk_row["cnt"]) if warning_risk_row else 0

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

            prev_median_ratio = None
            prev_severe_risk_count = None
            prev_warning_risk_count = None
            prev_total_high_alerts = None
            prev_total_symbols = None

            if previous_date:
                prev_rows = conn.execute(
                    "SELECT ratio FROM freefloat_snapshots WHERE report_date = ?",
                    (previous_date,),
                ).fetchall()
                prev_ratios = [float(r["ratio"]) for r in prev_rows]
                if prev_ratios:
                    prev_median_ratio = round(statistics.median(prev_ratios), 2)
                    prev_total_symbols = len(prev_ratios)

                prev_severe_risk_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio < 10.0",
                    (previous_date,),
                ).fetchone()
                prev_severe_risk_count = (
                    int(prev_severe_risk_row["cnt"]) if prev_severe_risk_row else 0
                )

                prev_warning_risk_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio >= 10.0 AND ratio < 20.0",
                    (previous_date,),
                ).fetchone()
                prev_warning_risk_count = (
                    int(prev_warning_risk_row["cnt"]) if prev_warning_risk_row else 0
                )

                prev_total_high_alerts_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_events WHERE report_date = ? AND severity = 'high'",
                    (previous_date,),
                ).fetchone()
                prev_total_high_alerts = (
                    int(prev_total_high_alerts_row["cnt"])
                    if prev_total_high_alerts_row
                    else 0
                )

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

        median_ratio = statistics.median(all_ratios)

        leaderboard = [
            {"code": r["code"], "name": r["name"], "ratio": float(r["ratio"])}
            for r in rows
        ]
        event_summary = [
            {"event_type": r["event_type"], "count": int(r["cnt"])} for r in event_rows
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
                "prev_median_ratio": prev_median_ratio,
                "prev_severe_risk_count": prev_severe_risk_count,
                "prev_warning_risk_count": prev_warning_risk_count,
                "prev_total_high_alerts": prev_total_high_alerts,
                "prev_total_symbols": prev_total_symbols,
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


@router.get("/api/market/sectors")
async def get_api_market_sectors(store: ArchiveStore = Depends(get_store)) -> Any:
    latest_date = get_latest_report_date(store)
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


@router.get("/api/sync/status")
async def get_api_sync_status(store: ArchiveStore = Depends(get_store)) -> Any:
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
                    cooldown_seconds_left = int((cooldown_time - now).total_seconds())
            except Exception:
                pass

        # Calculate coverage for the last 30 business days
        today = datetime.now(UTC).date()
        since_date = today - timedelta(days=30)

        # business days count
        total_biz = sum(
            1
            for ord_ in range(since_date.toordinal(), today.toordinal() + 1)
            if date.fromordinal(ord_).weekday() < 5
        )

        with store._connect() as conn:  # noqa: SLF001
            stored_row = conn.execute(
                "SELECT COUNT(DISTINCT report_date) AS n FROM freefloat_reports "
                "WHERE report_date BETWEEN ? AND ?",
                (since_date.isoformat(), today.isoformat()),
            ).fetchone()
            empty_row = conn.execute(
                "SELECT COUNT(*) AS n FROM freefloat_missing "
                "WHERE report_date BETWEEN ? AND ?",
                (since_date.isoformat(), today.isoformat()),
            ).fetchone()

        stored_cnt = int(stored_row["n"]) if stored_row else 0
        empty_cnt = int(empty_row["n"]) if empty_row else 0
        gaps_list = store.missing_business_days(since_date, today)

        coverage_pct = (
            round(((stored_cnt + empty_cnt) / total_biz * 100.0), 2)
            if total_biz > 0
            else 100.0
        )

        return {
            "sync_state": vap_state,
            "cooldown_active": cooldown_active,
            "cooldown_seconds_left": max(0, cooldown_seconds_left),
            "last_report_date": stats.get("last_report_date"),
            "reports_count": stats.get("reports"),
            "symbols_count": stats.get("symbols"),
            "health": {
                "since": since_date.isoformat(),
                "until": today.isoformat(),
                "total_business_days": total_biz,
                "stored_days": stored_cnt,
                "empty_days": empty_cnt,
                "coverage_pct": coverage_pct,
                "gaps": [g.isoformat() for g in gaps_list],
                "gap_count": len(gaps_list),
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


def _bg_sync(
    store: ArchiveStore,
    ws_manager: ConnectionManager,
    latest: bool = True,
    since: date | None = None,
    until: date | None = None,
) -> None:
    try:
        from ...layers import freefloat_archive

        logger.info("Background VAP free-float sync thread started")

        def progress_callback(progress_info: dict[str, Any]) -> None:
            try:
                anyio.from_thread.run(ws_manager.broadcast, progress_info)
            except Exception:
                pass

        # Also send the initial sync_started event
        anyio.from_thread.run(ws_manager.broadcast, {"event": "sync_started"})

        freefloat_archive.sync_archive(
            latest=latest,
            since=since,
            until=until,
            max_days=None,
            resume=True if since else False,
            store=store,
            on_progress=progress_callback,
        )
        logger.info("Background VAP free-float sync thread completed")
        anyio.from_thread.run(ws_manager.broadcast, {"event": "sync_completed"})
    except Exception as e:
        logger.exception("Background VAP free-float sync thread failed")
        anyio.from_thread.run(
            ws_manager.broadcast,
            {"event": "sync_failed", "error": str(e)},
        )


@router.post("/api/sync/run")
async def run_api_sync(
    payload: SyncRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    store: ArchiveStore = Depends(get_store),
    ws_manager: ConnectionManager = Depends(get_ws_manager),
) -> Any:
    try:
        logger.info("Starting VAP free-float sync via API")

        latest = payload.latest if payload else True
        since = None
        until = None
        if payload and payload.since:
            try:
                since = date.fromisoformat(payload.since)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid since date format (YYYY-MM-DD)"
                ) from exc
        if payload and payload.until:
            try:
                until = date.fromisoformat(payload.until)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid until date format (YYYY-MM-DD)"
                ) from exc

        # Only apply cooldown check for latest sync (to prevent spamming VAP home page)
        if latest:
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

        background_tasks.add_task(_bg_sync, store, ws_manager, latest, since, until)
        logger.info("Sync task queued successfully in background")
        return {"success": True, "message": "Sync started in background."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to start sync via API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
