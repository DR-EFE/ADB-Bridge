"""Adaptador delgado entre controles Flet y los servicios de Fase 2--4."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from ..services.adb_service import AdbService
from ..services.backup_profiles import get_backup_profiles
from ..services.backup_service import BackupService
from ..services.device_health_service import DeviceHealthService
from ..services.sync_service import SyncService
try:
    from ..services.file_explorer_service import FileExplorerService
    from ..services.file_explorer_models import ExplorerFilter
    FILE_EXPLORER_AVAILABLE = True
except ImportError:
    FileExplorerService = None
    ExplorerFilter = None
    FILE_EXPLORER_AVAILABLE = False
try:
    from ..services.automation_service import AutomationService
    from ..services.automation_rules import (
        ActionType, AutomationAction, AutomationCondition, AutomationRule, TriggerType,
    )
    AUTOMATION_AVAILABLE = True
except ImportError:
    AutomationService = None
    AUTOMATION_AVAILABLE = False
import uuid

class UiActionError(RuntimeError):
    """Error presentable al usuario; no reemplaza los errores del dominio."""

class IntegrationController:
    def __init__(self, device: Any, queue: Any, *, adb: Any = None, backup: Any = None, sync: Any = None, health: Any = None) -> None:
        self.device, self.queue = device, queue
        self.adb = adb or AdbService()
        self.backup = backup or BackupService()
        self.sync = sync or SyncService(self.adb, queue)
        self.health = health or DeviceHealthService(self.adb)
        self.explorer = FileExplorerService(self.adb, queue) if FILE_EXPLORER_AVAILABLE else None
        self.automation = AutomationService(job_queue=queue, sync_service=self.sync, backup_service=self.backup, adb_service=self.adb) if AUTOMATION_AVAILABLE else None
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

    def list_dir(self, path: str):
        if not self.explorer: raise UiActionError("Módulo explorador no disponible en esta copia.")
        return self.explorer.list_dir(path, serial=self.selected_serial())

    def search(self, root: str, pattern: str):
        if not self.explorer: raise UiActionError("Módulo explorador no disponible en esta copia.")
        return self.explorer.search(root, ExplorerFilter(name_glob=pattern or None), serial=self.selected_serial())

    def download_paths(self, paths, local_dir: str):
        if not self.explorer: raise UiActionError("Módulo explorador no disponible en esta copia.")
        result = self.explorer.download_paths(self.selected_serial(), paths, local_dir, enqueue_in_job_queue=True)
        return getattr(result, "job_id", None) or getattr(result, "message", None) or "descarga encolada"

    def automation_rules(self):
        if not self.automation: raise UiActionError("Módulo de automatizaciones no disponible en esta copia.")
        return self.automation.list_rules()

    def save_connected_rule(self, name: str, backup_profile: str = "", sync_profile: str = ""):
        if not self.automation: raise UiActionError("Módulo de automatizaciones no disponible en esta copia.")
        actions = []
        if backup_profile:
            actions.append(AutomationAction(action_type=ActionType.RUN_BACKUP_PROFILE, params={"profile_id": backup_profile}))
        if sync_profile:
            actions.append(AutomationAction(action_type=ActionType.RUN_SYNC_PROFILE, params={"profile_id": sync_profile}))
        rule = AutomationRule(id=f"rule_{uuid.uuid4().hex[:10]}", name=name, trigger=TriggerType.DEVICE_CONNECTED,
                              conditions=AutomationCondition(), actions=actions)
        self.automation.save_rule(rule)
        return rule

    def automation_runs(self):
        if not self.automation: raise UiActionError("Módulo de automatizaciones no disponible en esta copia.")
        return self.automation.list_runs(limit=20)
