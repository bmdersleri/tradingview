# ruff: noqa: B008, E501
from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from ...layers.freefloat_archive import ArchiveStore
from ...logging_utils import setup_logger
from ..dependencies import get_store

logger = setup_logger("tvcli.floatdash.market")

router = APIRouter(tags=["market"])


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


def _bg_sync(store: ArchiveStore) -> None:
    try:
        from ...layers import freefloat_archive

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


@router.post("/api/sync/run")
async def run_api_sync(
    background_tasks: BackgroundTasks, store: ArchiveStore = Depends(get_store)
) -> Any:
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

        background_tasks.add_task(_bg_sync, store)
        logger.info("Sync task queued successfully in background")
        return {"success": True, "message": "Sync started in background."}
    except Exception as e:
        logger.exception("Failed to start sync via API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
