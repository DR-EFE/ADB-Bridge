"""Centro operativo Flet: perfiles, salud y enlace a la cola global."""
from __future__ import annotations
import threading
from pathlib import Path
import flet as ft
from .. import theme
from ..controllers import IntegrationController, FILE_EXPLORER_AVAILABLE, AUTOMATION_AVAILABLE

class OperationsPage:
    def __init__(self, device, job_queue, *, page: ft.Page, on_log=None) -> None:
        self.page, self.on_log = page, on_log or (lambda *_: None)
        self.controller = IntegrationController(device, job_queue)
        self.destination = ft.TextField(label="Carpeta de respaldos", value=str(Path("backups").resolve()), expand=True)
        self.backup_profile, self.sync_profile = ft.Dropdown(label="Perfil de respaldo", expand=True), ft.Dropdown(label="Perfil de sincronización", expand=True)
        self.status = ft.Text("Elige un dispositivo en Dispositivos.", color=theme.TEXT_MUTED)
        self.health_result = ft.Text("Aún no se ha ejecutado un diagnóstico.", selectable=True)
        self.explorer_path = ft.TextField(label="Ruta remota", value="/sdcard", expand=True)
        self.explorer_pattern = ft.TextField(label="Buscar (glob)", hint_text="*.pdf", width=180)
        self.explorer_status = ft.Text("Selecciona un dispositivo para explorar." if FILE_EXPLORER_AVAILABLE else "Módulo no disponible en esta copia.", color=theme.TEXT_MUTED if FILE_EXPLORER_AVAILABLE else theme.WARN, size=11)
        self.explorer_items = ft.ListView(height=180, spacing=2)
        self.automation_name = ft.TextField(label="Nombre de regla", value="Backup al conectar", expand=True)
        self.automation_status = ft.Text("" if AUTOMATION_AVAILABLE else "Módulo no disponible en esta copia.", color=theme.TEXT_MUTED if AUTOMATION_AVAILABLE else theme.WARN, size=11)
        self.automation_rules_list = ft.Text("", selectable=True, size=11)
        self._load_profiles()
        self.view = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=14, controls=[
            theme.card(ft.Column([ft.Text("Centro de respaldos", size=18, weight=ft.FontWeight.BOLD), ft.Text("El avance, pausa, continuación y cancelación aparecen en la cola inferior.", color=theme.TEXT_MUTED, size=12), ft.Row([self.backup_profile, self.destination, ft.FilledButton("Encolar respaldo", icon=ft.Icons.BACKUP, on_click=self._enqueue_backup)])])),
            theme.card(ft.Column([ft.Text("Sincronización", size=18, weight=ft.FontWeight.BOLD), ft.Row([self.sync_profile, ft.FilledButton("Encolar sincronización", icon=ft.Icons.SYNC, on_click=self._enqueue_sync)]), ft.Text("Perfiles guardados por el servicio de sincronización.", color=theme.TEXT_MUTED, size=11)])),
            theme.card(ft.Column([ft.Text("Salud del dispositivo", size=18, weight=ft.FontWeight.BOLD), ft.Row([ft.FilledButton("Diagnosticar", icon=ft.Icons.MONITOR_HEART, on_click=self._diagnose), ft.Text("Guarda historial SQLite.", color=theme.TEXT_MUTED, size=12)]), self.health_result])),
            theme.card(ft.Column([ft.Text("Explorador avanzado", size=18, weight=ft.FontWeight.BOLD),
                ft.Row([self.explorer_path, ft.FilledButton("Listar", icon=ft.Icons.FOLDER_OPEN, on_click=self._list_directory), self.explorer_pattern, ft.OutlinedButton("Buscar", icon=ft.Icons.SEARCH, on_click=self._search_files)]),
                self.explorer_status, self.explorer_items, ft.Row([ft.Text("Los elementos seleccionados se descargan mediante la cola.", size=11, color=theme.TEXT_MUTED), ft.FilledButton("Descargar selección", icon=ft.Icons.DOWNLOAD, on_click=self._download_selection)])])),
            theme.card(ft.Column([ft.Text("Automatizaciones", size=18, weight=ft.FontWeight.BOLD),
                ft.Row([self.automation_name, ft.FilledButton("Guardar device_connected", icon=ft.Icons.ADD, on_click=self._save_rule), ft.OutlinedButton("Actualizar", icon=ft.Icons.REFRESH, on_click=self._refresh_rules)]),
                self.automation_status, self.automation_rules_list])), self.status])
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

    def _list_directory(self, _):
        def action():
            nodes = self.controller.list_dir(self.explorer_path.value or "/sdcard")
            self.explorer_items.controls = [ft.Checkbox(label=f"{'📁' if n.is_dir else '📄'} {n.name} ({n.formatted_size})", data=n.path) for n in nodes]
            self.explorer_status.value = f"{len(nodes)} elemento(s) en {self.explorer_path.value}"
            return "listado actualizado"
        self._background("Explorador", action)

    def _search_files(self, _):
        def action():
            result = self.controller.search(self.explorer_path.value or "/sdcard", self.explorer_pattern.value or "*")
            self.explorer_items.controls = [ft.Checkbox(label=f"📄 {n.path}", data=n.path) for n in result.items]
            self.explorer_status.value = f"{result.total_found} resultado(s)" + (" (truncado)" if result.truncated else "")
            return "búsqueda completada"
        self._background("Búsqueda", action)

    def _download_selection(self, _):
        paths = [c.data for c in self.explorer_items.controls if getattr(c, "value", False) and getattr(c, "data", None)]
        if not paths:
            self.explorer_status.value = "Selecciona al menos un archivo."; self.page.update(); return
        self._background("Descarga", lambda: self.controller.download_paths(paths, "exports/explorer"))

    def _save_rule(self, _):
        self._background("Regla", lambda: self.controller.save_connected_rule(self.automation_name.value or "Regla al conectar", self.backup_profile.value or "", self.sync_profile.value or ""))
        self._refresh_rules(None)

    def _refresh_rules(self, _):
        def action():
            rules = self.controller.automation_rules(); runs = self.controller.automation_runs()
            self.automation_rules_list.value = "Reglas:\n" + "\n".join(f"• {r.name} [{r.trigger.value}] {'activa' if r.enabled else 'inactiva'}" for r in rules)
            self.automation_rules_list.value += "\n\nRuns recientes:\n" + "\n".join(f"• {r.rule_id}: {r.status.value}" for r in runs[:10])
            return f"{len(rules)} regla(s), {len(runs)} run(s)"
        self._background("Automatizaciones", action)
    def shutdown(self): pass
