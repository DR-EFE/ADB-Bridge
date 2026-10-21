"""Router de sincronización inteligente de archivos (Fase 6B).

Endpoints:
- GET /api/sync/profiles: Lista los perfiles de sincronización incremental guardados.
- POST /api/sync/enqueue: Encola la ejecución de un perfil de sincronización en JobQueueService.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ui.services.adb_service import AdbService
from ui.services.sync_profiles import SyncProfile
from ui.services.sync_service import SyncService
from ..deps import get_adb_service, get_sync_service

logger = logging.getLogger("backend.routers.sync")

router = APIRouter(prefix="/api/sync", tags=["Sincronización"])


class SyncEnqueueRequest(BaseModel):
    profile_id: str = Field(..., min_length=1, description="Identificador del perfil de sincronización.")
    serial: Optional[str] = Field(default=None, description="Serial opcional del dispositivo para sobreescribir el del perfil.")


@router.get("/profiles", response_model=List[Dict[str, Any]], summary="Listar perfiles de sincronización")
def list_sync_profiles(
    sync_service: SyncService = Depends(get_sync_service),
) -> List[Dict[str, Any]]:
    """Devuelve la lista de perfiles de sincronización persistidos en la base de datos."""
    profiles: List[SyncProfile] = sync_service.list_profiles()
    return [p.model_dump() for p in profiles]


@router.post("/enqueue", status_code=status.HTTP_202_ACCEPTED, summary="Encolar trabajo de sincronización")
def enqueue_sync(
    req: SyncEnqueueRequest,
    adb_service: AdbService = Depends(get_adb_service),
    sync_service: SyncService = Depends(get_sync_service),
) -> Dict[str, Any]:
    """
    Encola un trabajo de sincronización incremental reanudable en JobQueueService.
    Valida la existencia del perfil y la disponibilidad del dispositivo conectado.
    """
    profile = sync_service.get_profile(req.profile_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Perfil de sincronización con ID '{req.profile_id}' no encontrado.",
        )

    # Determinar serial a utilizar
    target_serial = (req.serial or profile.serial or "").strip()
    if not target_serial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe especificar un serial de dispositivo ya sea en la solicitud o en el perfil de sincronización.",
        )

    # Validar dispositivo en ADB
    try:
        devices = adb_service.devices()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo consultando ADB: {exc}",
        )

    target_dev = next((d for d in devices if d.serial == target_serial), None)
    if not target_dev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El dispositivo '{target_serial}' no figura entre los dispositivos ADB conectados.",
        )
    if not target_dev.is_ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El dispositivo '{target_serial}' no está listo para sincronizar (estado: {target_dev.state.value}).",
        )

    # Si se sobreescribe el serial en la petición, crear copia del perfil con el nuevo serial
    if req.serial and req.serial != profile.serial:
        profile_data = profile.model_dump()
        profile_data["serial"] = req.serial
        profile_to_run = SyncProfile.model_validate(profile_data)
    else:
        profile_to_run = profile

    try:
        job_id = sync_service.enqueue_sync_job(profile_to_run)
        return {
            "job_id": job_id,
            "status": "pending",
            "profile_id": profile.id,
            "serial": target_serial,
            "direction": profile.direction.value,
            "message": f"Sincronización '{profile.name}' encolada exitosamente con ID {job_id}.",
        }
    except Exception as exc:
        logger.error("Error encolando sincronización para %s: %s", target_serial, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo al encolar trabajo de sincronización: {exc}",
        )
