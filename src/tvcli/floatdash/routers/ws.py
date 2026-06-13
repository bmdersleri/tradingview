# ruff: noqa: B008, E501
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...logging_utils import setup_logger
from ..dependencies import ConnectionManager

logger = setup_logger("tvcli.floatdash.ws")

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/sync")
async def websocket_endpoint(websocket: WebSocket) -> None:
    ws_manager: ConnectionManager = websocket.app.state.ws_manager
    await ws_manager.connect(websocket)
    logger.info("New WebSocket client connected")
    try:
        while True:
            # Keep the connection open by receiving data
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)
