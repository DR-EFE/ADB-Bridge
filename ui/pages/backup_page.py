"""Pestaña «Backup avanzado»: respaldo completo y granular sin root para Android."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

import flet as ft

from .. import theme
from ..services.backup_service import (
    BackupProgress,
    BackupService,
    CategoryDef,
    get_available_category_definitions,
    get_default_backup_dir,
)
from ..services.device_service import DeviceService

logger = logging.getLogger("ui.backup_page")


class BackupPage:
    """Vista de respaldo avanzado para categorías multimedia y de sistema."""

    def __init__(
        self,
        device: DeviceService,
        page: Optional[ft.Page] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        job_queue: Optional[Any] = None,
    ) -> None:
        self._device = device
        self.page = page
        self._on_log = on_log
        self._job_queue = job_queue

        self._categories: List[CategoryDef] = get_available_category_definitions()
        self._backup_service = BackupService(
            on_progress=self._on_backup_progress,
            on_log=self._on_service_log,
        )

        # Destino por defecto
        self._dest_dir = get_default_backup_dir(self._device.serial or "Dispositivo")
        self._dest_field = ft.TextField(
            value=str(self._dest_dir),
            label="Carpeta de destino en el PC",
            expand=True,
            text_size=12,
            read_only=False,
        )

        # Selectores nativos Flet
        self._dir_picker = ft.FilePicker(on_result=self._on_dir_picked)
        if self.page:
            self.page.overlay.append(self._dir_picker)

        # Componentes de progreso
        self._progress_bar = ft.ProgressBar(value=0.0, expand=True, color=theme.ACCENT, visible=False)
        self._status_text = ft.Text("Listo para iniciar respaldo.", size=13, color=theme.TEXT_MUTED)
        self._step_text = ft.Text("", size=11, color=theme.TEXT_MUTED)

        # Botones de control
        self._btn_start = ft.FilledButton(
            "Iniciar Respaldo",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_start_clicked,
        )
        self._btn_cancel = ft.OutlinedButton(
            "Cancelar",
            icon=ft.Icons.STOP,
            on_click=self._on_cancel_clicked,
            visible=False,
        )
        self._btn_open_folder = ft.OutlinedButton(
            "Abrir Carpeta",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._on_open_folder_clicked,
            visible=False,
        )

        # Checkboxes de categorías
        self._cat_checkboxes: Dict[str, ft.Checkbox] = {}
        cat_controls = []
        for cat in self._categories:
            cb = ft.Checkbox(
                label=cat.name,
                value=cat.enabled,
                on_change=lambda e, c=cat: self._on_category_toggled(c, e.control.value),
            )
            self._cat_checkboxes[cat.id] = cb
            cat_controls.append(
                theme.card(
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Column(
                                spacing=2,
                                expand=True,
                                controls=[
                                    cb,
                                    ft.Text(cat.description, size=11, color=theme.TEXT_MUTED),
                                ],
                            ),
                            ft.Container(
                                content=ft.Text("MULTIMEDIA" if cat.is_multimedia else "AVANZADO", size=10, weight=ft.FontWeight.BOLD),
                                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                border_radius=4,
                                bgcolor=theme.ACCENT if cat.is_multimedia else theme.SURFACE_ALT,
                            ),
                        ],
                    ),
                    padding=8,
                )
            )

        self._categories_column = ft.Column(
            spacing=6,
            controls=cat_controls,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # Resumen / Resultados
        self._results_column = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)

        self.view = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                # Barra superior con selección de destino y modo rápido
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                spacing=8,
                                controls=[
                                    self._dest_field,
                                    ft.OutlinedButton(
                                        "📁 Elegir Carpeta...",
                                        on_click=lambda _: self._dir_picker.get_directory_path(
                                            dialog_title="Seleccionar carpeta de respaldo"
                                        ),
                                    ),
                                ],
                            ),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Row(
                                        spacing=8,
                                        controls=[
                                            ft.FilledButton(
                                                "⭐ Backup Completo (Todo)",
                                                icon=ft.Icons.BACKUP,
                                                on_click=lambda _: self._select_all_categories(True),
                                            ),
                                            ft.OutlinedButton(
                                                "Solo Multimedia",
                                                icon=ft.Icons.PHOTO_LIBRARY,
                                                on_click=lambda _: self._select_multimedia_only(),
                                            ),
                                            ft.TextButton(
                                                "Deseleccionar todo",
                                                on_click=lambda _: self._select_all_categories(False),
                                            ),
                                        ],
                                    ),
                                    ft.Row(
                                        spacing=8,
                                        controls=[
                                            self._btn_start,
                                            self._btn_cancel,
                                            self._btn_open_folder,
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    padding=12,
                ),
                # Barra de progreso
                theme.card(
                    ft.Column(
                        spacing=6,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[self._status_text, self._step_text],
                            ),
                            self._progress_bar,
                        ],
                    ),
                    padding=10,
                ),
                # Contenido principal: categorías con scroll
                self._categories_column,
            ],
        )

    def _log(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)

    def _on_service_log(self, msg: str, level: str) -> None:
        color = theme.ERR if level == "ERROR" else (theme.WARN if level == "WARN" else theme.TEXT)
        self._log(msg, color)

    def _on_dir_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.path:
            self._dest_dir = Path(e.path)
            self._dest_field.value = str(self._dest_dir)
            try:
                self._dest_field.update()
            except Exception:
                pass

    def _on_category_toggled(self, cat: CategoryDef, value: bool) -> None:
        cat.enabled = bool(value)

    def _select_all_categories(self, enabled: bool) -> None:
        for cat in self._categories:
            cat.enabled = enabled
            if cat.id in self._cat_checkboxes:
                self._cat_checkboxes[cat.id].value = enabled
        try:
            self._categories_column.update()
        except Exception:
            pass

    def _select_multimedia_only(self) -> None:
        for cat in self._categories:
            cat.enabled = cat.is_multimedia
            if cat.id in self._cat_checkboxes:
                self._cat_checkboxes[cat.id].value = cat.is_multimedia
        try:
            self._categories_column.update()
        except Exception:
            pass

    def _on_backup_progress(self, progress: BackupProgress) -> None:
        self._progress_bar.value = progress.percent
        self._progress_bar.visible = progress.status == "RUNNING"
        self._status_text.value = f"Estado: {progress.status} | Categoría: {progress.current_category or '—'}"
        self._step_text.value = progress.current_step

        if progress.status in ("COMPLETED", "OK"):
            self._status_text.color = theme.OK
            self._btn_start.visible = True
            self._btn_cancel.visible = False
            self._btn_open_folder.visible = True
        elif progress.status == "PARTIAL":
            self._status_text.color = theme.WARN
            self._btn_start.visible = True
            self._btn_cancel.visible = False
            self._btn_open_folder.visible = True
        elif progress.status == "CANCELLED":
            self._status_text.color = theme.TEXT_MUTED
            self._btn_start.visible = True
            self._btn_cancel.visible = False
            self._btn_open_folder.visible = True
        elif progress.status == "FAILED" or progress.status == "ERROR":
            self._status_text.color = theme.ERR
            self._btn_start.visible = True
            self._btn_cancel.visible = False
        else:
            self._status_text.color = theme.ACCENT

        try:
            self._progress_bar.update()
            self._status_text.update()
            self._step_text.update()
            self._btn_start.update()
            self._btn_cancel.update()
            self._btn_open_folder.update()
        except Exception:
            pass

    def _on_start_clicked(self, _e: ft.ControlEvent) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            msg = "Hay varios teléfonos conectados. Selecciona uno obligatoriamente en el panel lateral antes de respaldar."
            self._status_text.value = msg
            self._status_text.color = theme.ERR
            self._status_text.update()
            self._log(msg, theme.ERR)
            return

        if not status.connected or not self._device.serial:
            msg = status.error or "No hay ningún dispositivo ADB conectado para realizar el respaldo."
            self._status_text.value = msg
            self._status_text.color = theme.ERR
            self._status_text.update()
            self._log(msg, theme.ERR)
            return

        enabled_cats = [c for c in self._categories if c.enabled]
        if not enabled_cats:
            self._status_text.value = "Selecciona al menos una categoría para respaldar."
            self._status_text.color = theme.WARN
            self._status_text.update()
            return

        target_path_str = (self._dest_field.value or "").strip()
        if not target_path_str:
            target_path = get_default_backup_dir(self._device.serial)
            self._dest_field.value = str(target_path)
            self._dest_field.update()
        else:
            target_path = Path(target_path_str)

        self._dest_dir = target_path
        self._btn_start.visible = False
        self._btn_cancel.visible = True
        self._btn_open_folder.visible = False
        self._progress_bar.visible = True
        self._progress_bar.value = 0.0
        self._status_text.value = "Iniciando respaldo en segundo plano..."
        self._status_text.color = theme.ACCENT
        self._btn_start.update()
        self._btn_cancel.update()
        self._btn_open_folder.update()
        self._progress_bar.update()
        self._status_text.update()

        serial = self._device.serial
        model = status.model

        def worker():
            try:
                # Si job_queue está disponible, registrar el trabajo
                jid = None
                if self._job_queue:
                    cat_names = ", ".join(c.name for c in enabled_cats)
                    jid = self._job_queue.enqueue(
                        job_type=JobType.BACKUP_CATEGORY,
                        title=f"Respaldo: {cat_names}",
                        serial=serial,
                        payload={"destination": str(target_path), "categories": [c.id for c in enabled_cats]},
                    )

                self._backup_service.run_backup(
                    serial=serial,
                    device_label=model,
                    destination_dir=target_path,
                    categories=self._categories,
                )

                if self._job_queue and jid:
                    job = self._job_queue.get_job(jid)
                    if job:
                        job.status = JobStatus.COMPLETED
                        job.progress_percent = 1.0
                        job.current_step = "Respaldo finalizado con éxito"
                        self._job_queue._notify(job)
                        self._job_queue._persist_history()

            except Exception as exc:
                logger.error("Error inesperado en backup worker: %s", exc)
                self._status_text.value = f"Error crítico: {exc}"
                self._status_text.color = theme.ERR
                self._btn_start.visible = True
                self._btn_cancel.visible = False
                if self._job_queue and 'jid' in locals() and jid:
                    job = self._job_queue.get_job(jid)
                    if job:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        self._job_queue._notify(job)
                        self._job_queue._persist_history()
                try:
                    self._status_text.update()
                    self._btn_start.update()
                    self._btn_cancel.update()
                except Exception:
                    pass

        threading.Thread(target=worker, name="backup-worker", daemon=True).start()

    def _on_cancel_clicked(self, _e: ft.ControlEvent) -> None:
        self._status_text.value = "Cancelando respaldo... preservando archivos transferidos."
        self._status_text.color = theme.WARN
        self._status_text.update()
        self._backup_service.cancel()

    def _on_open_folder_clicked(self, _e: ft.ControlEvent) -> None:
        if self._dest_dir.exists():
            os.startfile(str(self._dest_dir.resolve()))
        else:
            self._dest_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self._dest_dir.resolve()))

    def shutdown(self) -> None:
        """Detiene cualquier proceso activo al cerrar la aplicación."""
        if self._backup_service.is_running():
            self._backup_service.cancel()
