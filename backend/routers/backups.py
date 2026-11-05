"""Router de gestión y ejecución de respaldos (Fase 6B).

Endpoints:
- GET /api/backups/profiles: Lista los perfiles disponibles del Centro de respaldos.
- POST /api/backups/enqueue: Encola un trabajo de respaldo para un dispositivo específico.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.infrastructure.adb.adb_service import AdbService
from core.domain.rules.backup_profiles import BackupProfile, get_backup_profiles
from core.services.backup_service import BackupService, get_default_backup_dir
from core.services.job_queue_service import JobQueueService
from ..deps import get_adb_service, get_backup_service, get_job_queue_service

logger = logging.getLogger("backend.routers.backups")

router = APIRouter(prefix="/api/backups", tags=["Respaldos"])


class BackupEnqueueRequest(BaseModel):
    serial: str = Field(..., min_length=1, description="Serial explícito del dispositivo objetivo.")
    profile_id: str = Field(..., min_length=1, description="Identificador del perfil de respaldo a ejecutar.")
    destination_dir: Optional[str] = Field(default=None, description="Directorio local destino opcional.")


@router.get("/profiles", summary="Listar perfiles de respaldo disponibles")
def list_backup_profiles() -> List[Dict[str, Any]]:
    """Devuelve la lista de perfiles declarativos configurados en el Centro de Respaldos."""
    profiles = get_backup_profiles()
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "items_count": len(p.items),
            "items": [{"id": it.id, "label": it.label, "kind": it.kind} for it in p.items],
        }
        for p in profiles.values()
    ]


@router.post("/enqueue", status_code=status.HTTP_202_ACCEPTED, summary="Encolar trabajo de respaldo")
def enqueue_backup(
    req: BackupEnqueueRequest,
    adb_service: AdbService = Depends(get_adb_service),
    backup_service: BackupService = Depends(get_backup_service),
    queue_service: JobQueueService = Depends(get_job_queue_service),
) -> Dict[str, Any]:
    """
    Encola la ejecución de un perfil de respaldo en JobQueueService.
    Verifica que el serial esté presente y el dispositivo esté listo.
    """
    serial = req.serial.strip()
    if not serial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe especificar el serial del dispositivo en el cuerpo de la solicitud.",
        )

    # Validar dispositivo en ADB
    try:
        devices = adb_service.devices()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo consultando ADB: {exc}",
        )

    target_dev = next((d for d in devices if d.serial == serial), None)
    if not target_dev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El dispositivo '{serial}' no figura entre los dispositivos ADB conectados.",
        )
    if not target_dev.is_ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El dispositivo '{serial}' no está en estado listo (estado: {target_dev.state.value}).",
        )

    # Validar perfil
    profiles = get_backup_profiles()
    if req.profile_id not in profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Perfil de respaldo '{req.profile_id}' no encontrado. Opciones válidas: {list(profiles.keys())}",
        )

    profile_obj = profiles[req.profile_id]
    dest_path = Path(req.destination_dir) if req.destination_dir else get_default_backup_dir(serial)

    try:
        job_id = backup_service.enqueue_profile(
            queue_service=queue_service,
            profile=profile_obj,
            serial=serial,
            adb_service=adb_service,
            destination_dir=dest_path,
        )
        return {
            "job_id": job_id,
            "status": "pending",
            "profile_id": req.profile_id,
            "serial": serial,
            "destination_dir": str(dest_path),
            "message": f"Respaldo '{profile_obj.label}' encolado exitosamente con ID {job_id}.",
        }
    except Exception as exc:
        logger.error("Error encolando respaldo para %s: %s", serial, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo al encolar trabajo de respaldo: {exc}",
        )
