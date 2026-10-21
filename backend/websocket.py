"""Gestor y rutas de WebSockets para seguimiento de progreso de trabajos en tiempo real (Fase 6B).

Provee:
- /ws/jobs: Stream de eventos para todos los trabajos encolados y en ejecución.
- /ws/jobs/{job_id}: Stream dedicado para un trabajo específico hasta su finalización.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ui.services.job_queue_service import JobItem, JobQueueService, JobStatus
from .deps import get_job_queue_service

logger = logging.getLogger("backend.websocket")

ws_router = APIRouter(tags=["WebSockets"])


class JobWebSocketManager:
    """Administra conexiones WebSocket y distribuye eventos provenientes de JobQueueService."""

    def __init__(self) -> None:
        # Clientes escuchando todos los trabajos
        self._global_connections: Set[WebSocket] = set()
        # Clientes escuchando un job_id específico: job_id -> Set[WebSocket]
        self._job_connections: Dict[str, Set[WebSocket]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listener_registered = False

    def setup_listener(self, queue_service: JobQueueService, loop: asyncio.AbstractEventLoop) -> None:
        """Configura el loop de asyncio y registra el listener con la cola de trabajos."""
        self._loop = loop
        if not self._listener_registered:
            queue_service.add_listener(self._on_job_event)
            self._listener_registered = True
            logger.info("JobWebSocketManager: Listener de eventos registrado en JobQueueService.")

    def teardown_listener(self, queue_service: JobQueueService) -> None:
        """Desregistra el listener al apagar la aplicación."""
        if self._listener_registered:
            try:
                queue_service.remove_listener(self._on_job_event)
            except Exception:
                pass
            self._listener_registered = False

    def _on_job_event(self, job: JobItem) -> None:
        """Callback ejecutado desde el worker thread de JobQueueService."""
        if not self._loop or not self._loop.is_running():
            return

        payload = {
            "event": "job_update",
            "job_id": job.id,
            "status": job.status.value if isinstance(job.status, JobStatus) else str(job.status),
            "progress_percent": job.progress_percent,
            "current_step": job.current_step,
            "error": job.error,
            "bytes_total": job.bytes_total,
            "bytes_completed": job.bytes_completed,
            "files_total": job.files_total,
            "files_completed": job.files_completed,
            "job": job.to_dict(),
        }

        # Despachar al bucle de eventos de asyncio de forma segura entre hilos
        asyncio.run_coroutine_threadsafe(self.broadcast(job.id, payload), self._loop)

    async def connect_global(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._global_connections.add(websocket)
        logger.debug("Cliente WebSocket global conectado. Total: %d", len(self._global_connections))

    def disconnect_global(self, websocket: WebSocket) -> None:
        self._global_connections.discard(websocket)
        logger.debug("Cliente WebSocket global desconectado. Total: %d", len(self._global_connections))

    async def connect_job(self, websocket: WebSocket, job_id: str) -> None:
        await websocket.accept()
        if job_id not in self._job_connections:
            self._job_connections[job_id] = set()
        self._job_connections[job_id].add(websocket)
        logger.debug("Cliente WebSocket conectado a job %s. Total: %d", job_id, len(self._job_connections[job_id]))

    def disconnect_job(self, websocket: WebSocket, job_id: str) -> None:
        if job_id in self._job_connections:
            self._job_connections[job_id].discard(websocket)
            if not self._job_connections[job_id]:
                del self._job_connections[job_id]
        logger.debug("Cliente WebSocket desconectado de job %s", job_id)

    async def broadcast(self, job_id: str, data: Dict[str, Any]) -> None:
        """Envía el evento a todos los clientes globales y a los clientes específicos de job_id."""
        # 1. Enviar a clientes globales
        dead_global = set()
        for ws in list(self._global_connections):
            try:
                await ws.send_json(data)
            except Exception:
                dead_global.add(ws)
        self._global_connections.difference_update(dead_global)

        # 2. Enviar a clientes específicos del job_id
        if job_id in self._job_connections:
            dead_job = set()
            for ws in list(self._job_connections[job_id]):
                try:
                    await ws.send_json(data)
                except Exception:
                    dead_job.add(ws)
            self._job_connections[job_id].difference_update(dead_job)


# Singleton del gestor WebSocket
manager = JobWebSocketManager()


@ws_router.websocket("/ws/jobs")
async def websocket_jobs_all(
    websocket: WebSocket,
    queue_service: JobQueueService = Depends(get_job_queue_service),
):
    """
    WebSocket que emite en tiempo real todas las actualizaciones de cualquier trabajo
    en la cola JobQueueService.
    """
    await manager.connect_global(websocket)
    try:
        # Enviar estado inicial de bienvenida con conteo de trabajos actuales
        active_jobs = queue_service.get_active_jobs()
        await websocket.send_json({
            "event": "connected",
            "message": "Suscrito a eventos globales de JobQueueService",
            "active_jobs_count": len(active_jobs),
        })
        while True:
            # Mantener conexión abierta leyendo mensajes o pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_global(websocket)
    except Exception as exc:
        logger.debug("Error en WebSocket global: %s", exc)
        manager.disconnect_global(websocket)


@ws_router.websocket("/ws/jobs/{job_id}")
async def websocket_job_single(
    websocket: WebSocket,
    job_id: str,
    queue_service: JobQueueService = Depends(get_job_queue_service),
):
    """
    WebSocket dedicado para un trabajo específico.
    Envía inmediatamente el estado actual del trabajo y luego emite eventos ante cada progreso.
    """
    job = queue_service.get_job(job_id)
    if not job:
        await websocket.accept()
        await websocket.send_json({
            "event": "error",
            "message": f"El trabajo '{job_id}' no existe en JobQueueService.",
        })
        await websocket.close(code=4004)
        return

    await manager.connect_job(websocket, job_id)
    try:
        # Enviar estado actual del trabajo de inmediato
        await websocket.send_json({
            "event": "job_init",
            "job_id": job.id,
            "job": job.to_dict(),
        })

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_job(websocket, job_id)
    except Exception as exc:
        logger.debug("Error en WebSocket de job %s: %s", job_id, exc)
        manager.disconnect_job(websocket, job_id)
