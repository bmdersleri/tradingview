from __future__ import annotations

from typing import cast

from fastapi import Request

from ..layers.freefloat_archive import ArchiveStore


def get_store(request: Request) -> ArchiveStore:
    """FastAPI dependency to retrieve the active ArchiveStore from app state."""
    return cast(ArchiveStore, request.app.state.store)
