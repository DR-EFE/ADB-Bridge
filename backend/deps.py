"""Inyección de dependencias para los routers de FastAPI (Fase 6B).

Permite desacoplar los endpoints de las instancias concretas de los servicios
facilitando su reemplazo con mocks en pruebas automatizadas vía TestClient.
"""

from __future__ import annotations

from typing import Optional

from core.infrastructure.adb.adb_service import AdbService
from core.services.backup_service import BackupService
from core.services.device_health_service import DeviceHealthService
from core.services.file_explorer_service import FileExplorerService
from core.services.job_queue_service import JobQueueService
from core.services.sync_service import SyncService

# Instancias compartidas por defecto
_adb_service: Optional[AdbService] = None
_job_queue_service: Optional[JobQueueService] = None
_backup_service: Optional[BackupService] = None
_sync_service: Optional[SyncService] = None
_device_health_service: Optional[DeviceHealthService] = None
_file_explorer_service: Optional[FileExplorerService] = None


def get_adb_service() -> AdbService:
    global _adb_service
    if _adb_service is None:
        _adb_service = AdbService()
    return _adb_service


def get_job_queue_service() -> JobQueueService:
    global _job_queue_service
    if _job_queue_service is None:
        _job_queue_service = JobQueueService()
    return _job_queue_service


def get_backup_service() -> BackupService:
    global _backup_service
    if _backup_service is None:
        _backup_service = BackupService()
    return _backup_service


def get_sync_service() -> SyncService:
    global _sync_service
    if _sync_service is None:
        _sync_service = SyncService(
            adb_service=get_adb_service(),
            job_queue=get_job_queue_service(),
        )
    return _sync_service


def get_device_health_service() -> DeviceHealthService:
    global _device_health_service
    if _device_health_service is None:
        _device_health_service = DeviceHealthService(
            adb_service=get_adb_service(),
        )
    return _device_health_service


def get_file_explorer_service() -> FileExplorerService:
    global _file_explorer_service
    if _file_explorer_service is None:
        _file_explorer_service = FileExplorerService(
            adb_service=get_adb_service(),
            job_queue_service=get_job_queue_service(),
        )
    return _file_explorer_service


def reset_dependencies() -> None:
    """Restablece los singletons para aislamiento entre tests."""
    global _adb_service, _job_queue_service, _backup_service
    global _sync_service, _device_health_service, _file_explorer_service
    _adb_service = None
    _job_queue_service = None
    _backup_service = None
    _sync_service = None
    _device_health_service = None
    _file_explorer_service = None
