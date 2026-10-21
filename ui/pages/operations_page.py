"""Centro operativo Flet: perfiles, salud y enlace a la cola global."""
from __future__ import annotations
import threading
from pathlib import Path
import flet as ft
from .. import theme
from ..controllers import IntegrationController

class OperationsPage:
    def __init__(self, device, job_queue, *, page: ft.Page, on_log=None) -> None:
        self.page, self.on_log = page, on_log or (lambda *_: None)
        self.controller = IntegrationController(device, job_queue)
        self.destination = ft.TextField(label="Carpeta de respaldos", value=str(Path("backups").resolve()), expand=True)
        self.backup_profile, self.sync_profile = ft.Dropdown(label="Perfil de respaldo", expand=True), ft.Dropdown(label="Perfil de sincronización", expand=True)
        self.status = ft.Text("Elige un dispositivo en Dispositivos.", color=theme.TEXT_MUTED)
        self.health_result = ft.Text("Aún no se ha ejecutado un diagnóstico.", selectable=True)
        self._load_profiles()
        self.view = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=14, controls=[
            theme.card(ft.Column([ft.Text("Centro de respaldos", size=18, weight=ft.FontWeight.BOLD), ft.Text("El avance, pausa, continuación y cancelación aparecen en la cola inferior.", color=theme.TEXT_MUTED, size=12), ft.Row([self.backup_profile, self.destination, ft.FilledButton("Encolar respaldo", icon=ft.Icons.BACKUP, on_click=self._enqueue_backup)])])),
            theme.card(ft.Column([ft.Text("Sincronización", size=18, weight=ft.FontWeight.BOLD), ft.Row([self.sync_profile, ft.FilledButton("Encolar sincronización", icon=ft.Icons.SYNC, on_click=self._enqueue_sync)]), ft.Text("Perfiles guardados por el servicio de sincronización.", color=theme.TEXT_MUTED, size=11)])),
            theme.card(ft.Column([ft.Text("Salud del dispositivo", size=18, weight=ft.FontWeight.BOLD), ft.Row([ft.FilledButton("Diagnosticar", icon=ft.Icons.MONITOR_HEART, on_click=self._diagnose), ft.Text("Guarda historial SQLite.", color=theme.TEXT_MUTED, size=12)]), self.health_result])),
            theme.card(ft.Column([ft.Text("Automatizaciones", size=18, weight=ft.FontWeight.BOLD), ft.Text("automation_service no está presente en esta copia; no se simulan reglas ni ejecuciones.", color=theme.WARN, size=12)])), self.status])
    def _load_profiles(self) -> None:
        backups = self.controller.backup_profiles(); self.backup_profile.options = [ft.DropdownOption(key=p.id, text=p.label) for p in backups]
        if backups: self.backup_profile.value = backups[0].id
        try: syncs = self.controller.sync_profiles()
        except Exception: syncs = []
        self._sync_by_id = {p.id: p for p in syncs}; self.sync_profile.options = [ft.DropdownOption(key=p.id, text=f"{p.name} · {p.direction.value}") for p in syncs]
        if syncs: self.sync_profile.value = syncs[0].id
    def _background(self, label, action):
        self.status.value = f"{label}…"; self.page.update()
        def run():
            try:
                result = action(); self.status.value = f"{label}: {result}"; self.on_log(self.status.value, theme.OK)
            except Exception as exc:
                self.status.value = f"{label}: {exc}"; self.on_log(self.status.value, theme.ERR)
            try: self.page.update()
            except Exception: pass
        threading.Thread(target=run, name="ui-operations", daemon=True).start()
    def _enqueue_backup(self, _):
        if self.backup_profile.value: self._background("Backup encolado", lambda: self.controller.enqueue_backup(self.backup_profile.value, self.destination.value))
    def _enqueue_sync(self, _):
        profile = self._sync_by_id.get(self.sync_profile.value)
        if profile: self._background("Sincronización encolada", lambda: self.controller.enqueue_sync(profile))
    def _diagnose(self, _):
        def action():
            snapshot, trends = self.controller.diagnose(); data = snapshot.to_dict()
            self.health_result.value = "\n".join([f"Estado: {data['overall_status']}", f"Batería: {data['battery_level']}%", f"Almacenamiento libre: {data['storage_free_bytes']}", f"Latencia ADB: {data['adb_latency_ms']} ms"] + [f"{k}: último={v.latest}, promedio={v.average}" for k, v in trends.trends.items()]); return "snapshot guardado"
        self._background("Diagnóstico", action)
    def shutdown(self): pass
