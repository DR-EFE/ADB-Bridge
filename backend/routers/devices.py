"""Router de gestión de dispositivos y diagnóstico de salud (Fase 6B).

Endpoints:
- GET /api/devices: Lista todos los dispositivos ADB conectados y su estado.
- GET /api/devices/{serial}/health: Obtiene un snapshot diagnóstico de salud del dispositivo.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from ui.services.adb_models import Device, DeviceState
from ui.services.adb_service import AdbService
from ui.services.device_health_service import DeviceHealthService
from ..deps import get_adb_service, get_device_health_service

logger = logging.getLogger("backend.routers.devices")

router = APIRouter(prefix="/api/devices", tags=["Dispositivos"])


@router.get("", response_model=List[Dict[str, Any]], summary="Listar dispositivos ADB")
def list_devices(
    adb_service: AdbService = Depends(get_adb_service),
) -> List[Dict[str, Any]]:
    """Consulta 'adb devices -l' y retorna la lista estructurada de dispositivos detectados."""
    try:
        devices: List[Device] = adb_service.devices()
        return [
            {
                "serial": d.serial,
                "state": d.state.value if isinstance(d.state, DeviceState) else str(d.state),
                "model": d.model,
                "usb_serial": d.usb_serial,
                "is_ready": d.is_ready,
                "is_network": d.is_network,
                "friendly_name": d.friendly_name,
                "battery_level": d.battery_level,
                "android_version": d.android_version,
            }
            for d in devices
        ]
    except Exception as exc:
        logger.error("Error al consultar dispositivos ADB: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error consultando dispositivos ADB: {exc}",
        )


@router.get("/{serial}/health", summary="Snapshot diagnóstico de salud del dispositivo")
def get_device_health(
    serial: str,
    adb_service: AdbService = Depends(get_adb_service),
    health_service: DeviceHealthService = Depends(get_device_health_service),
) -> Dict[str, Any]:
    """
    Obtiene el diagnóstico de salud de un dispositivo específico:
    batería, almacenamiento, memoria RAM, red, versión y latencia ADB.
    """
    clean_serial = serial.strip()
    if not clean_serial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe proporcionar un serial de dispositivo válido.",
        )

    # Verificar que el dispositivo figure en ADB y esté en estado device
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
            detail=f"El dispositivo '{clean_serial}' no está listo para diagnósticos (estado: {target_dev.state.value}).",
        )

    try:
        snapshot = health_service.collect_snapshot(clean_serial)
        return snapshot.to_dict()
    except Exception as exc:
        logger.error("Error recolectando salud para %s: %s", clean_serial, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error recopilando métricas de salud: {exc}",
        )
