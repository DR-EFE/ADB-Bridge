"""Aplicación principal FastAPI y servidor backend local (Fase 6B).

Inicia la API REST local y el servidor WebSocket para control de operaciones
desde la misma máquina (127.0.0.1) con bajo acoplamiento a los servicios de dominio.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import get_job_queue_service
from .routers import (
    backups_router,
    devices_router,
    explorer_router,
    jobs_router,
    sync_router,
)
from .websocket import manager, ws_router

logger = logging.getLogger("backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida de la aplicación: inicializa WebSockets y listeners en segundo plano."""
    loop = asyncio.get_running_loop()
    job_queue = get_job_queue_service()
    manager.setup_listener(job_queue, loop)
    logger.info("FastAPI Backend iniciado. Escuchando en loop de eventos.")
    yield
    manager.teardown_listener(job_queue)
    logger.info("FastAPI Backend detenido.")


def create_app() -> FastAPI:
    """Fábrica de la aplicación FastAPI."""
    app_instance = FastAPI(
        title="Android Management Local API",
        description="API REST y WebSockets de control local para administración Android, respaldos, sync y explorador.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Permitir solicitudes locales (CORS)
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Registro de routers
    app_instance.include_router(devices_router)
    app_instance.include_router(backups_router)
    app_instance.include_router(sync_router)
    app_instance.include_router(jobs_router)
    app_instance.include_router(explorer_router)
    app_instance.include_router(ws_router)

    @app_instance.get("/", tags=["Estado"])
    def root():
        return {
            "name": "Android Management Local API",
            "version": "1.0.0",
            "status": "online",
            "docs": "/docs",
            "endpoints": [
                "/api/devices",
                "/api/devices/{serial}/health",
                "/api/backups/profiles",
                "/api/backups/enqueue",
                "/api/sync/profiles",
                "/api/sync/enqueue",
                "/api/jobs",
                "/api/explorer/list",
                "/ws/jobs",
                "/ws/jobs/{job_id}",
            ],
        }

    return app_instance


app = create_app()


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Inicia el servidor uvicorn con bind seguro a localhost por defecto."""
    logger.info("Iniciando servidor local en http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start_server()
