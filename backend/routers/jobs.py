"""Router de control y consulta de trabajos en JobQueueService (Fase 6B).

Endpoints:
- GET /api/jobs: Lista todos los trabajos encolados y del historial con filtros opcionales.
- GET /api/jobs/{job_id}: Consulta el estado detallado de un trabajo individual.
- POST /api/jobs/{job_id}/pause: Pausa un trabajo en ejecución o pendiente.
- POST /api/jobs/{job_id}/resume: Reanuda un trabajo pausado.
- POST /api/jobs/{job_id}/cancel: Cancela cooperativamente un trabajo.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.domain.models.jobs import JobItem, JobStatus
from core.services.job_queue_service import JobQueueService
from ..deps import get_job_queue_service

logger = logging.getLogger("backend.routers.jobs")

router = APIRouter(prefix="/api/jobs", tags=["Trabajos"])


@router.get("", response_model=List[Dict[str, Any]], summary="Listar trabajos")
def list_jobs(
    status_filter: Optional[str] = Query(default=None, alias="status", description="Filtrar por estado (pending, running, paused, completed, failed, cancelled)"),
    serial_filter: Optional[str] = Query(default=None, alias="serial", description="Filtrar por serial del dispositivo"),
    limit: int = Query(default=100, ge=1, le=500, description="Límite máximo de resultados"),
    queue_service: JobQueueService = Depends(get_job_queue_service),
) -> List[Dict[str, Any]]:
    """Consulta la lista de trabajos activos y archivados en JobQueueService."""
    jobs: List[JobItem] = queue_service.get_history(limit=limit)

    if status_filter:
        stat_lower = status_filter.strip().lower()
        jobs = [j for j in jobs if (j.status.value if isinstance(j.status, JobStatus) else str(j.status)).lower() == stat_lower]

    if serial_filter:
        ser_clean = serial_filter.strip()
        jobs = [j for j in jobs if j.serial == ser_clean]

    return [j.to_dict() for j in jobs]


@router.get("/{job_id}", response_model=Dict[str, Any], summary="Consultar trabajo por ID")
def get_job(
    job_id: str,
    queue_service: JobQueueService = Depends(get_job_queue_service),
) -> Dict[str, Any]:
    """Obtiene los detalles, progreso y métricas de un trabajo específico."""
    job = queue_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trabajo con ID '{job_id}' no encontrado en JobQueueService.",
        )
    return job.to_dict()


@router.post("/{job_id}/pause", summary="Pausar trabajo")
def pause_job(
    job_id: str,
    queue_service: JobQueueService = Depends(get_job_queue_service),
) -> Dict[str, Any]:
    """Pausa la ejecución de un trabajo activo o en cola conservando su progreso."""
    job = queue_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trabajo con ID '{job_id}' no encontrado.",
        )

    success = queue_service.pause(job_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede pausar el trabajo '{job_id}' en su estado actual ({job.status.value}).",
        )

    return {
        "job_id": job_id,
        "status": job.status.value,
        "message": f"Trabajo '{job_id}' pausado exitosamente.",
    }


@router.post("/{job_id}/resume", summary="Reanudar trabajo pausado")
def resume_job(
    job_id: str,
    queue_service: JobQueueService = Depends(get_job_queue_service),
) -> Dict[str, Any]:
    """Reanuda la ejecución de un trabajo previamente pausado."""
    job = queue_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trabajo con ID '{job_id}' no encontrado.",
        )

    success = queue_service.resume(job_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede reanudar el trabajo '{job_id}' (estado actual: {job.status.value}).",
        )

    return {
        "job_id": job_id,
        "status": job.status.value,
        "message": f"Trabajo '{job_id}' reanudado exitosamente.",
    }


@router.post("/{job_id}/cancel", summary="Cancelar trabajo")
def cancel_job(
    job_id: str,
    queue_service: JobQueueService = Depends(get_job_queue_service),
) -> Dict[str, Any]:
    """Cancela cooperativamente un trabajo encolado o en ejecución."""
    job = queue_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trabajo con ID '{job_id}' no encontrado.",
        )

    success = queue_service.cancel(job_id)
    if not success and job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El trabajo '{job_id}' ya se encuentra finalizado ({job.status.value}).",
        )

    return {
        "job_id": job_id,
        "status": job.status.value,
        "message": f"Cancelación del trabajo '{job_id}' solicitada/ejecutada exitosamente.",
    }
