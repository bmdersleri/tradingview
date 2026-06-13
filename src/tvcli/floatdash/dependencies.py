# ruff: noqa: E501
from __future__ import annotations

from typing import Any, cast

from fastapi import Request, WebSocket

from ..layers.freefloat_archive import ArchiveStore


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


def get_store(request: Request) -> ArchiveStore:
    """FastAPI dependency to retrieve the active ArchiveStore from app state."""
    return cast(ArchiveStore, request.app.state.store)


def get_ws_manager(request: Request) -> ConnectionManager:
    """FastAPI dependency to retrieve the active ConnectionManager from app state."""
    return cast(ConnectionManager, request.app.state.ws_manager)
