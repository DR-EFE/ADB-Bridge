"""Pestaña «Backup avanzado»: respaldo completo y granular sin root para Android,
con procesamiento visual estructurado, exportación a Excel/JSON y consulta en tablas.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from .. import theme
from ..services.backup_processor import BackupProcessedResult, BackupProcessor, CategoryProcessedData
from ..services.backup_service import (
    BackupProgress,
    BackupService,
    CategoryDef,
    get_available_category_definitions,
    get_default_backup_dir,
)
from ..services.device_service import DeviceService
from ..services.job_queue_service import JobStatus, JobType

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
            self.page.services.append(self._dir_picker)

        # Componentes de progreso
        self._progress_bar = ft.ProgressBar(value=0.0, expand=True, color=theme.ACCENT, visible=False)
        self._status_text = ft.Text("Listo para iniciar respaldo.", size=13, color=theme.TEXT_MUTED)
        self._step_text = ft.Text("", size=11, color=theme.TEXT_MUTED)

        # Botones de control principales
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

        # Botones para procesamiento visual y exportación
        self._btn_process = ft.FilledButton(
            "Procesar backup",
            icon=ft.Icons.TRANSFORM,
            on_click=self._on_process_clicked,
            style=ft.ButtonStyle(bgcolor=theme.ACCENT, color=theme.TEXT),
        )
        self._btn_open_excel = ft.OutlinedButton(
            "Abrir Excel",
            icon=ft.Icons.TABLE_CHART,
            on_click=self._on_open_excel_clicked,
            visible=False,
        )
        self._btn_open_json = ft.OutlinedButton(
            "Abrir JSON",
            icon=ft.Icons.DATA_OBJECT,
            on_click=self._on_open_json_clicked,
            visible=False,
        )

        # Checkboxes de categorías para el respaldo
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
                                content=ft.Text(
                                    "MULTIMEDIA" if cat.is_multimedia else "AVANZADO",
                                    size=10,
                                    weight=ft.FontWeight.BOLD,
                                ),
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

        # Contenedores para la visualización del backup procesado
        self._processed_data: Optional[BackupProcessedResult] = None
        self._summary_cards_row = ft.Row(wrap=True, spacing=10)
        self._category_selector = ft.Dropdown(
            label="Categoría a consultar",
            options=[],
            width=260,
            text_size=12,
            on_select=self._on_category_selected_for_view,
        )
        self._table_container = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

        self._processed_view_panel = ft.Column(
            spacing=10,
            expand=True,
            visible=False,
            controls=[
                theme.card(
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text("Resumen por Categoría del Backup", size=14, weight=ft.FontWeight.BOLD),
                                    ft.Row(
                                        spacing=6,
                                        controls=[
                                            self._btn_open_excel,
                                            self._btn_open_json,
                                        ],
                                    ),
                                ],
                            ),
                            self._summary_cards_row,
                        ],
                    ),
                    padding=12,
                ),
                theme.card(
                    ft.Column(
                        spacing=8,
                        expand=True,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text("Explorador de Datos Procesados", size=13, weight=ft.FontWeight.W_600),
                                    self._category_selector,
                                ],
                            ),
                            self._table_container,
                        ],
                    ),
                    padding=12,
                    expand=True,
                ),
            ],
        )

        self.view = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                # Barra superior con selección de destino
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                spacing=8,
                                controls=[
                                    self._dest_field,
                                    ft.OutlinedButton(
                                        "Elegir Carpeta...",
                                        icon=ft.Icons.FOLDER_OPEN,
                                        on_click=lambda _: self._dir_picker.get_directory_path(
                                            dialog_title="Seleccionar carpeta de respaldo"
                                        ),
                                    ),
                                ],
                            ),
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                wrap=True,
                                controls=[
                                    ft.Row(
                                        spacing=8,
                                        wrap=True,
                                        controls=[
                                            ft.FilledButton(
                                                "Backup Completo (Todo)",
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
                                        wrap=True,
                                        controls=[
                                            self._btn_start,
                                            self._btn_cancel,
                                            self._btn_process,
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
                # Paneles inferiores divididos o conmutables
                self._processed_view_panel,
                self._categories_column,
            ],
        )

        # Si ya existe backup en la carpeta destino, revisar si ya está procesado
        self._check_existing_backup()

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
            self._check_existing_backup()
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

        if progress.status in ("COMPLETED", "OK", "PARTIAL", "CANCELLED"):
            self._status_text.color = theme.OK if progress.status in ("COMPLETED", "OK") else theme.WARN
            self._btn_start.visible = True
            self._btn_cancel.visible = False
            self._btn_open_folder.visible = True
            # Cargar automáticamente los datos procesados
            self._load_and_display_processed()
        elif progress.status in ("FAILED", "ERROR"):
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
                        job.current_step = "Respaldo y procesamiento finalizados con éxito"
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
        target = self._dest_dir
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target.resolve()))

    # =========================================================================
    # Procesamiento posterior visual y exportaciones
    # =========================================================================

    def _on_process_clicked(self, _e: ft.ControlEvent) -> None:
        target_path_str = (self._dest_field.value or "").strip()
        if not target_path_str:
            self._status_text.value = "Especifica una carpeta de backup válida para procesar."
            self._status_text.color = theme.WARN
            self._status_text.update()
            return

        target = Path(target_path_str)
        if not target.exists():
            self._status_text.value = f"La carpeta no existe: {target}"
            self._status_text.color = theme.ERR
            self._status_text.update()
            return

        self._status_text.value = "Procesando archivos de backup y generando Excel/JSON..."
        self._status_text.color = theme.ACCENT
        self._status_text.update()

        def worker():
            try:
                processor = BackupProcessor(target)
                self._processed_data = processor.process()
                self._status_text.value = f"Backup procesado con éxito. Estado: {self._processed_data.overall_status}"
                self._status_text.color = theme.OK
                self._load_and_display_processed()
            except Exception as exc:
                logger.error("Error al procesar backup: %s", exc)
                self._status_text.value = f"Error al procesar: {exc}"
                self._status_text.color = theme.ERR
                try:
                    self._status_text.update()
                except Exception:
                    pass

        threading.Thread(target=worker, name="reprocess-worker", daemon=True).start()

    def _on_open_excel_clicked(self, _e: ft.ControlEvent) -> None:
        if self._processed_data and self._processed_data.xlsx_path:
            xlsx = Path(self._processed_data.xlsx_path)
            if xlsx.exists():
                os.startfile(str(xlsx.resolve()))
                return
        xlsx_default = self._dest_dir / "Procesado" / "backup_procesado.xlsx"
        if xlsx_default.exists():
            os.startfile(str(xlsx_default.resolve()))

    def _on_open_json_clicked(self, _e: ft.ControlEvent) -> None:
        if self._processed_data and self._processed_data.json_path:
            json_p = Path(self._processed_data.json_path)
            if json_p.exists():
                os.startfile(str(json_p.resolve()))
                return
        json_default = self._dest_dir / "Procesado" / "backup_procesado.json"
        if json_default.exists():
            os.startfile(str(json_default.resolve()))

    def _check_existing_backup(self) -> None:
        proc_json = self._dest_dir / "Procesado" / "backup_procesado.json"
        if proc_json.exists():
            try:
                data = json.loads(proc_json.read_text(encoding="utf-8"))
                cats = {
                    k: CategoryProcessedData(
                        id=v["id"],
                        name=v["name"],
                        status=v["status"],
                        total_records=v.get("total_records", 0),
                        empty_records=v.get("empty_records", 0),
                        error_count=v.get("error_count", 0),
                        denied_count=v.get("denied_count", 0),
                        raw_files=v.get("raw_files", []),
                        records=v.get("records", []),
                        raw_fallback=v.get("raw_fallback"),
                        note=v.get("note"),
                    )
                    for k, v in data.get("categories", {}).items()
                }
                self._processed_data = BackupProcessedResult(
                    backup_dir=data.get("backup_dir", str(self._dest_dir)),
                    processed_at=data.get("processed_at", ""),
                    overall_status=data.get("overall_status", "UNKNOWN"),
                    categories=cats,
                    json_path=data.get("json_path"),
                    xlsx_path=data.get("xlsx_path"),
                )
                self._render_processed_views()
            except Exception as exc:
                logger.debug("No se pudo cargar json existente: %s", exc)

    def _load_and_display_processed(self) -> None:
        proc_json = self._dest_dir / "Procesado" / "backup_procesado.json"
        if proc_json.exists() and not self._processed_data:
            self._check_existing_backup()
        elif self._processed_data:
            self._render_processed_views()

    def _render_processed_views(self) -> None:
        if not self._processed_data:
            return

        self._btn_open_excel.visible = bool(self._processed_data.xlsx_path and Path(self._processed_data.xlsx_path).exists())
        self._btn_open_json.visible = bool(self._processed_data.json_path and Path(self._processed_data.json_path).exists())
        self._btn_open_folder.visible = True

        # Renderizar tarjetas de resumen por categoría
        cards = []
        dropdown_opts = []
        for cat_id, cat_data in self._processed_data.categories.items():
            status_color = (
                theme.OK if cat_data.status == "OK"
                else (theme.WARN if cat_data.status in ("PARTIAL", "EMPTY")
                else theme.ERR)
            )

            card_ctrl = ft.Container(
                content=ft.Column(
                    spacing=2,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(cat_data.name, weight=ft.FontWeight.BOLD, size=12),
                                ft.Container(
                                    content=ft.Text(cat_data.status, size=9, weight=ft.FontWeight.BOLD, color=theme.BG),
                                    bgcolor=status_color,
                                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                    border_radius=4,
                                ),
                            ],
                        ),
                        ft.Text(f"Registros: {cat_data.total_records}", size=11, color=theme.TEXT),
                        ft.Text(
                            f"Archivos: {len(cat_data.raw_files)} | Denegados: {cat_data.denied_count} | Errores: {cat_data.error_count}",
                            size=10,
                            color=theme.TEXT_MUTED,
                        ),
                    ],
                ),
                bgcolor=theme.SURFACE_ALT,
                padding=8,
                border_radius=8,
                border=ft.Border.all(1, theme.BORDER),
                width=240,
            )
            cards.append(card_ctrl)
            dropdown_opts.append(ft.dropdown.Option(cat_id, f"{cat_data.name} ({cat_data.total_records})"))

        self._summary_cards_row.controls = cards
        self._category_selector.options = dropdown_opts
        if dropdown_opts and not self._category_selector.value:
            self._category_selector.value = dropdown_opts[0].key

        self._processed_view_panel.visible = True
        self._update_table_for_selected_category()

        try:
            self._summary_cards_row.update()
            self._category_selector.update()
            self._btn_open_excel.update()
            self._btn_open_json.update()
            self._btn_open_folder.update()
            self._processed_view_panel.update()
            self.view.update()
        except Exception:
            pass

    def _on_category_selected_for_view(self, _e: ft.ControlEvent) -> None:
        self._update_table_for_selected_category()

    def _update_table_for_selected_category(self) -> None:
        if not self._processed_data or not self._category_selector.value:
            return

        cat_id = self._category_selector.value
        cat_data = self._processed_data.categories.get(cat_id)
        if not cat_data:
            return

        self._table_container.controls.clear()

        # Si hay nota explicativa
        if cat_data.note:
            self._table_container.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=theme.WARN),
                        ft.Text(cat_data.note, size=12, color=theme.WARN, expand=True),
                    ]),
                    padding=8,
                    bgcolor=theme.SURFACE_ALT,
                    border_radius=6,
                )
            )

        # Si hay registros estructurados, renderizar DataTable navegable
        if cat_data.records:
            headers = list(cat_data.records[0].keys())
            columns = [ft.DataColumn(ft.Text(h, weight=ft.FontWeight.BOLD, size=11)) for h in headers]
            rows = []
            for rec in cat_data.records[:200]:  # Limitar a 200 filas visibles para fluidez en UI
                cells = [
                    ft.DataCell(
                        ft.Text(
                            str(rec.get(h, "")),
                            size=11,
                            selectable=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            max_lines=2,
                        )
                    )
                    for h in headers
                ]
                rows.append(ft.DataRow(cells=cells))

            dt = ft.DataTable(
                columns=columns,
                rows=rows,
                border=ft.Border.all(1, theme.BORDER),
                horizontal_lines=ft.BorderSide(1, theme.BORDER),
                column_spacing=16,
                data_row_min_height=32,
                data_row_max_height=48,
            )
            count_label = ft.Text(
                f"Mostrando {min(len(cat_data.records), 200)} de {cat_data.total_records} registros.",
                size=11,
                color=theme.TEXT_MUTED,
            )
            self._table_container.controls.extend([
                count_label,
                ft.Row([dt], scroll=ft.ScrollMode.AUTO),
            ])
        elif cat_data.raw_fallback:
            # Mostrar como texto original cuando solo está disponible el raw
            self._table_container.controls.extend([
                ft.Text("Contenido original no estructurado:", size=11, weight=ft.FontWeight.BOLD),
                ft.TextField(
                    value=cat_data.raw_fallback,
                    multiline=True,
                    read_only=True,
                    expand=True,
                    text_size=11,
                    min_lines=8,
                    max_lines=16,
                ),
            ])
        else:
            self._table_container.controls.append(
                ft.Text(
                    f"No hay registros disponibles en esta categoría (Estado: {cat_data.status}).",
                    color=theme.TEXT_MUTED,
                    size=12,
                )
            )

        try:
            self._table_container.update()
        except Exception:
            pass

    def shutdown(self) -> None:
        """Detiene cualquier proceso activo al cerrar la aplicación."""
        if self._backup_service.is_running():
            self._backup_service.cancel()
