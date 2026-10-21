"""Adaptador delgado entre controles Flet y los servicios de Fase 2--4."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from ..services.adb_service import AdbService
from ..services.backup_profiles import get_backup_profiles
from ..services.backup_service import BackupService
from ..services.device_health_service import DeviceHealthService
from ..services.sync_service import SyncService

class UiActionError(RuntimeError):
    """Error presentable al usuario; no reemplaza los errores del dominio."""

class IntegrationController:
    def __init__(self, device: Any, queue: Any, *, adb: Any = None, backup: Any = None, sync: Any = None, health: Any = None) -> None:
        self.device, self.queue = device, queue
        self.adb = adb or AdbService()
        self.backup = backup or BackupService()
        self.sync = sync or SyncService(self.adb, queue)
        self.health = health or DeviceHealthService(self.adb)
    def selected_serial(self) -> str:
        serial = getattr(self.device, "serial", None)
        if not serial:
            raise UiActionError("Selecciona explícitamente un dispositivo antes de continuar.")
        try: return self.adb.resolve_serial(serial)
        except Exception as exc: raise UiActionError(str(exc)) from exc
    def backup_profiles(self): return list(get_backup_profiles().values())
    def enqueue_backup(self, profile_id: str, destination: str) -> str:
        return self.backup.enqueue_profile(self.queue, profile_id, serial=self.selected_serial(), adb_service=self.adb, destination_dir=Path(destination).expanduser())
    def sync_profiles(self): return self.sync.list_profiles()
    def enqueue_sync(self, profile: Any) -> str:
        return self.sync.enqueue_sync_job(profile.model_copy(update={"serial": self.selected_serial()}), self.queue)
    def diagnose(self):
        serial = self.selected_serial()
        return self.health.collect_snapshot(serial), self.health.get_trends(serial, days=7)
