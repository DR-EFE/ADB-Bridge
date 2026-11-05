"""Router de exploración de archivos remotos en Android ADB (Fase 6B).

Endpoints:
- GET /api/explorer/list: Lista los archivos y subdirectorios de una ruta remota.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.infrastructure.adb.adb_service import AdbService
from core.domain.models.explorer import FileNode
from core.services.file_explorer_service import FileExplorerService
from ..deps import get_adb_service, get_file_explorer_service

logger = logging.getLogger("backend.routers.explorer")

router = APIRouter(prefix="/api/explorer", tags=["Explorador"])


@router.get("/list", response_model=List[Dict[str, Any]], summary="Listar directorio remoto")
def list_remote_directory(
    serial: str = Query(..., min_length=1, description="Serial obligatorio del dispositivo Android conectado"),
    path: str = Query(default="/sdcard", description="Ruta absoluta remota en el dispositivo (ej: /sdcard o /sdcard/Download)"),
    adb_service: AdbService = Depends(get_adb_service),
    explorer_service: FileExplorerService = Depends(get_file_explorer_service),
) -> List[Dict[str, Any]]:
    """
    Lista el contenido de una carpeta en el dispositivo remoto mediante FileExplorerService.
    Requiere serial explícito y valida disponibilidad del dispositivo.
    """
    clean_serial = serial.strip()
    if not clean_serial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe proporcionar un parámetro 'serial' válido y no vacío.",
        )

    # Validar dispositivo en ADB
    try:
        devices = adb_service.devices()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo consultando ADB: {exc}",
        )

    target_dev = next((d for d in devices if d.serial == clean_serial), None)
    if not target_dev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El dispositivo con serial '{clean_serial}' no fue encontrado en ADB.",
        )
    if not target_dev.is_ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El dispositivo '{clean_serial}' no está listo para exploración (estado: {target_dev.state.value}).",
        )

    try:
        nodes: List[FileNode] = explorer_service.list_dir(path=path, serial=clean_serial)
        return [n.model_dump() for n in nodes]
    except Exception as exc:
        logger.error("Error al listar '%s' en [%s]: %s", path, clean_serial, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo al listar ruta remota: {exc}",
        )
