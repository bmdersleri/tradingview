# ruff: noqa: B008, E501
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ...layers.freefloat_archive import ArchiveStore
from ..dependencies import get_store

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/history")
async def get_alerts_history(
    symbol: str | None = None,
    severity: str | None = None,
    status_filter: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    store: ArchiveStore = Depends(get_store),
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
                    "status": row["status"] if row["status"] is not None else "sent",
                }
            )
        return results
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
