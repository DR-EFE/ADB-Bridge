"""Routers de la API REST local."""

from .devices import router as devices_router
from .backups import router as backups_router
from .sync import router as sync_router
from .jobs import router as jobs_router
from .explorer import router as explorer_router

__all__ = [
    "devices_router",
    "backups_router",
    "sync_router",
    "jobs_router",
    "explorer_router",
]
