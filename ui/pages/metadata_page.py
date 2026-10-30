"""Pestaña «Metadatos & Mapa Forense»: extracción, normalización, análisis geoespacial
y visualización interactiva de metadatos EXIF/GPS desde la computadora.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService
from ..services.photo_metadata_service import PhotoMetadataService
from core.domain.models.photo_metadata import (
    MetadataAnalysisSummary,
    MetadataCluster,
    MetadataSessionResult,
    PhotoMetadataRecord,
)

logger = logging.getLogger("ui.metadata_page")

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".dng", ".tiff", ".tif", ".bmp"}
ARCHIVE_EXTS = {".zip", ".rar", ".tar", ".7z", ".tgz", ".gz"}


def scan_pc_photo_directories() -> List[Dict[str, Any]]:
    """Detecta carpetas en la computadora que contengan imágenes descargadas."""
    results: List[Dict[str, Any]] = []
    user_home = Path.home()
    search_dirs = [
        user_home / "Downloads" / "AndroidBackup",
        user_home / "Downloads",
        user_home / "Pictures",
        user_home / "Imágenes",
    ]

    seen = set()
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            # Revisar la carpeta raíz
            imgs_in_root = sum(1 for f in base.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS)
            if imgs_in_root > 0 and str(base) not in seen:
                seen.add(str(base))
                results.append({
                    "name": f"{base.name} ({imgs_in_root} fotos en PC)",
                    "path": str(base),
                    "count": imgs_in_root,
                })

            # Revisar subcarpetas inmediatas (ej. DCIM, WhatsApp Images, respaldos)
            for sub in base.iterdir():
                if sub.is_dir() and not sub.name.startswith("."):
                    sub_count = sum(1 for f in sub.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS)
                    if sub_count > 0 and str(sub) not in seen:
                        seen.add(str(sub))
                        results.append({
                            "name": f"{sub.name} ({sub_count} fotos)",
                            "path": str(sub),
                            "count": sub_count,
                        })
        except Exception:
            pass

    return results


class MetadataPage:
    """Vista interactiva para el análisis forense de metadatos fotográficos seleccionados desde el PC."""

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

        self._service = PhotoMetadataService(
            on_log=self._on_service_log,
            on_progress=self._on_service_progress,
        )

        self._current_result: Optional[MetadataSessionResult] = None
        self._is_analyzing = False
        self._filter_mode = "all"  # 'all', 'gps', 'nogps', 'edited'
        self._search_query = ""

        # ---------------------------------------------------------------------
        # FilePickers Nativos de Flet
        # ---------------------------------------------------------------------
        self._image_picker = ft.FilePicker(on_result=self._on_single_image_picked)
        self._folder_picker = ft.FilePicker(on_result=self._on_folder_picked)
        self._archive_picker = ft.FilePicker(on_result=self._on_archive_picked)

        if self.page:
            self.page.services.extend([self._image_picker, self._folder_picker, self._archive_picker])

        # ---------------------------------------------------------------------
        # Controles de Entrada y Selección desde la Computadora
        # ---------------------------------------------------------------------
        self._input_mode = ft.RadioGroup(
            content=ft.Row(
                spacing=16,
                wrap=True,
                controls=[
                    ft.Radio(value="folder", label="📁 Carpeta con imágenes en el PC (Recomendado)"),
                    ft.Radio(value="archive", label="📦 Archivo comprimido (.zip, .rar, .tar) en el PC"),
                    ft.Radio(value="single", label="📸 Imagen individual en el PC"),
                    ft.Radio(value="device", label="📱 Extraer directo desde celular (ADB)"),
                ],
            ),
            value="folder",
            on_change=self._on_mode_change,
        )

        self._path_field = ft.TextField(
            label="Ruta en la computadora (haga clic en 'Examinar en mi PC' o pegue la ruta)",
            hint_text="Ej: C:\\Users\\...\\Downloads\\WhatsApp Images o D:\\Fotos_Celular",
            expand=True,
            text_size=12,
            dense=True,
            on_change=self._on_path_text_changed,
        )

        self._browse_btn = ft.FilledButton(
            "Examinar en mi PC...",
            icon=ft.Icons.DESKTOP_WINDOWS,
            on_click=self._on_browse_click,
        )

        # Dropdowns de ayuda y acceso rápido en la computadora
        self._pc_folders_dropdown = ft.Dropdown(
            label="Carpetas detectadas en este PC",
            hint_text="Descargas del celular detectadas...",
            width=280,
            text_size=12,
            dense=True,
            on_select=self._on_pc_folder_selected,
        )

        self._sessions_dropdown = ft.Dropdown(
            label="Cargar análisis previo",
            hint_text="Sesiones previas guardadas...",
            width=260,
            text_size=12,
            dense=True,
            on_select=self._on_session_selected,
        )

        # Banner de resumen previo (indica fotos detectadas antes de ejecutar)
        self._preview_info_banner = ft.Container(
            visible=False,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            bgcolor=theme.ACCENT_LIGHT,
            border_radius=8,
            border=ft.Border.all(1, theme.ACCENT),
            content=ft.Row(
                spacing=8,
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=theme.ACCENT),
                    ft.Text("", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT),
                ],
            ),
        )

        self._device_reuse_chk = ft.Checkbox(
            label="Reutilizar descarga previa del celular en Downloads/AndroidBackup",
            value=True,
            visible=False,
        )

        # ---------------------------------------------------------------------
        # Barra de Acción y Progreso
        # ---------------------------------------------------------------------
        self._start_btn = ft.FilledButton(
            "Iniciar Análisis de Metadatos & Mapa",
            icon=ft.Icons.TRAVEL_EXPLORE,
            on_click=self._on_start_analysis,
        )

        self._cancel_btn = ft.OutlinedButton(
            "Cancelar",
            icon=ft.Icons.CANCEL_OUTLINED,
            disabled=True,
            on_click=self._on_cancel_analysis,
        )

        self._download_tool_btn = ft.OutlinedButton(
            "Descargar ExifTool portátil",
            icon=ft.Icons.DOWNLOAD_FOR_OFFLINE_OUTLINED,
            tooltip="Descarga la utilidad oficial portable en metadata\\exiftool.exe",
            on_click=self._on_download_tool_click,
        )

        self._progress_bar = ft.ProgressBar(value=0.0, expand=True, color=theme.ACCENT, visible=False)
        self._status_text = ft.Text("Seleccione una carpeta o archivo en su computadora para iniciar.", size=12, color=theme.TEXT_MUTED)

        # ---------------------------------------------------------------------
        # Botones de Acceso Rápido a Visores (HTML y Carpeta)
        # ---------------------------------------------------------------------
        self._btn_open_map = ft.FilledButton(
            "Abrir Mapa Interactivo",
            icon=ft.Icons.MAP,
            disabled=True,
            on_click=lambda _e: self._open_external_file(getattr(self._current_result, "map_html_path", None)),
        )

        self._btn_open_dash = ft.FilledButton(
            "Abrir Dashboard Forense",
            icon=ft.Icons.ANALYTICS_OUTLINED,
            disabled=True,
            on_click=lambda _e: self._open_external_file(getattr(self._current_result, "dashboard_html_path", None)),
        )

        self._btn_open_folder = ft.OutlinedButton(
            "Ver Carpeta de Salida",
            icon=ft.Icons.FOLDER,
            disabled=True,
            on_click=lambda _e: self._open_external_file(getattr(self._current_result, "session_dir", None)),
        )

        # ---------------------------------------------------------------------
        # Tarjetas de Estadísticas (KPIs)
        # ---------------------------------------------------------------------
        self._kpi_total = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color=theme.TEXT)
        self._kpi_gps = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color=theme.OK)
        self._kpi_gps_pct = ft.Text("0% del total", size=11, color=theme.OK)
        self._kpi_nogps = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color=theme.WARN)
        self._kpi_devices = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color=theme.ACCENT)
        self._kpi_dates = ft.Text("Sin datos", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT)

        # ---------------------------------------------------------------------
        # Contenedores de Sub-Pestañas
        # ---------------------------------------------------------------------
        self._clusters_container = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        self._devices_summary_container = ft.Row(spacing=8, wrap=True)

        self._search_field = ft.TextField(
            label="Buscar por nombre, dispositivo o fecha...",
            prefix_icon=ft.Icons.SEARCH,
            expand=True,
            dense=True,
            text_size=12,
            on_change=self._on_search_change,
        )

        self._photos_list_view = ft.ListView(expand=True, spacing=6)
        self._evidence_artifacts_column = ft.Column(spacing=8)
        self._manifest_info_text = ft.Text("No hay sesión cargada.", size=12, color=theme.TEXT_MUTED)

        # Sub-tabs compatibles con Flet 1.0.4
        self._sub_tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="Clusters Geoespaciales", icon=ft.Icons.PIN_DROP_OUTLINED),
                ft.Tab(label="Catálogo de Fotografías", icon=ft.Icons.PHOTO_LIBRARY_OUTLINED),
                ft.Tab(label="Cadena de Custodia & Evidencia", icon=ft.Icons.SECURITY_OUTLINED),
            ]
        )
        self._sub_tabs = ft.Tabs(
            length=3,
            selected_index=0,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    self._sub_tab_bar,
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            self._build_clusters_tab_content(),
                            self._build_photos_tab_content(),
                            self._build_evidence_tab_content(),
                        ],
                    ),
                ],
            ),
        )

        # Construir layout principal
        self.view = self._build_view()
        self._refresh_detected_pc_folders()
        self._refresh_sessions_dropdown()

    # -------------------------------------------------------------------------
    # UI BUILDERS
    # -------------------------------------------------------------------------
    def _build_view(self) -> ft.Control:
        kpi_row = ft.Row(
            spacing=12,
            controls=[
                theme.card(
                    ft.Column(
                        [
                            ft.Row([ft.Icon(ft.Icons.IMAGE, size=18, color=theme.TEXT_MUTED), ft.Text("Total Fotos", size=12, color=theme.TEXT_MUTED)]),
                            self._kpi_total,
                        ],
                        spacing=2,
                    ),
                    expand=1,
                    padding=10,
                ),
                theme.card(
                    ft.Column(
                        [
                            ft.Row([ft.Icon(ft.Icons.LOCATION_ON, size=18, color=theme.OK), ft.Text("Con GPS", size=12, color=theme.TEXT_MUTED)]),
                            ft.Row([self._kpi_gps, self._kpi_gps_pct], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.END),
                        ],
                        spacing=2,
                    ),
                    expand=1,
                    padding=10,
                ),
                theme.card(
                    ft.Column(
                        [
                            ft.Row([ft.Icon(ft.Icons.LOCATION_OFF, size=18, color=theme.WARN), ft.Text("Sin Coordenadas", size=12, color=theme.TEXT_MUTED)]),
                            self._kpi_nogps,
                        ],
                        spacing=2,
                    ),
                    expand=1,
                    padding=10,
                ),
                theme.card(
                    ft.Column(
                        [
                            ft.Row([ft.Icon(ft.Icons.PHONE_ANDROID, size=18, color=theme.ACCENT), ft.Text("Dispositivos", size=12, color=theme.TEXT_MUTED)]),
                            self._kpi_devices,
                        ],
                        spacing=2,
                    ),
                    expand=1,
                    padding=10,
                ),
                theme.card(
                    ft.Column(
                        [
                            ft.Row([ft.Icon(ft.Icons.DATE_RANGE, size=18, color=theme.TEXT_MUTED), ft.Text("Rango Temporal", size=12, color=theme.TEXT_MUTED)]),
                            self._kpi_dates,
                        ],
                        spacing=2,
                    ),
                    expand=2,
                    padding=10,
                ),
            ],
        )

        controls_card = theme.card(
            ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            self._input_mode,
                            ft.Row([self._pc_folders_dropdown, self._sessions_dropdown], spacing=8),
                        ],
                    ),
                    ft.Row(
                        controls=[
                            self._path_field,
                            self._browse_btn,
                        ],
                    ),
                    self._preview_info_banner,
                    self._device_reuse_chk,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row([self._start_btn, self._cancel_btn, self._download_tool_btn], spacing=8),
                            ft.Row([self._btn_open_map, self._btn_open_dash, self._btn_open_folder], spacing=8),
                        ],
                    ),
                    ft.Row([self._progress_bar], expand=True),
                    self._status_text,
                ],
            ),
            padding=14,
        )

        return ft.Column(
            expand=True,
            spacing=12,
            controls=[
                controls_card,
                kpi_row,
                ft.Container(content=self._sub_tabs, expand=True),
            ],
        )

    def _build_clusters_tab_content(self) -> ft.Control:
        return ft.Container(
            padding=10,
            content=ft.Column(
                expand=True,
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Text("Dispositivos Identificados", size=13, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                    self._devices_summary_container,
                    ft.Divider(height=1, color=theme.BORDER),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Top Concentraciones Geográficas (Radio aproximado de ~110m)", size=13, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                            ft.Text("Agrupación por coordenadas rounded a 3 decimales", size=11, color=theme.TEXT_MUTED),
                        ],
                    ),
                    self._clusters_container,
                ],
            ),
        )

    def _build_photos_tab_content(self) -> ft.Control:
        filter_chips = ft.Row(
            spacing=8,
            controls=[
                ft.Text("Filtros rápidos:", size=11, color=theme.TEXT_MUTED),
                ft.Chip(label=ft.Text("Todas"), selected=True, on_click=lambda _e: self._set_filter("all")),
                ft.Chip(label=ft.Text("Solo con GPS"), on_click=lambda _e: self._set_filter("gps")),
                ft.Chip(label=ft.Text("Sin GPS"), on_click=lambda _e: self._set_filter("nogps")),
                ft.Chip(label=ft.Text("Editadas / Software"), on_click=lambda _e: self._set_filter("edited")),
            ],
        )

        return ft.Container(
            padding=10,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    ft.Row([self._search_field, filter_chips]),
                    ft.Container(content=self._photos_list_view, expand=True),
                ],
            ),
        )

    def _build_evidence_tab_content(self) -> ft.Control:
        btn_verify = ft.FilledButton(
            "Verificar Integridad SHA-256",
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            on_click=self._on_verify_integrity_click,
        )

        return ft.Container(
            padding=10,
            content=ft.Column(
                expand=True,
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Cadena de Custodia & Manifiesto Criptográfico", size=14, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                            btn_verify,
                        ],
                    ),
                    self._manifest_info_text,
                    ft.Divider(height=1, color=theme.BORDER),
                    ft.Text("Artefactos Sellados (Hashes SHA-256):", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    self._evidence_artifacts_column,
                ],
            ),
        )

    # -------------------------------------------------------------------------
    # DETECCION DE CARPETAS Y PREVISUALIZACION
    # -------------------------------------------------------------------------
    def _refresh_detected_pc_folders(self) -> None:
        folders = scan_pc_photo_directories()
        options = []
        for f in folders[:10]:
            options.append(ft.dropdown.Option(key=f["path"], text=f["name"]))
        self._pc_folders_dropdown.options = options
        if folders and not self._path_field.value:
            # Sugerir la primera carpeta detectada si el campo está vacío
            first = folders[0]["path"]
            self._path_field.value = first
            self._update_selection_preview(first)

    def _on_pc_folder_selected(self, _e: ft.ControlEvent) -> None:
        chosen = self._pc_folders_dropdown.value
        if chosen:
            self._path_field.value = chosen
            self._input_mode.value = "folder"
            self._update_selection_preview(chosen)
            try:
                self.view.update()
            except Exception:
                pass

    def _on_path_text_changed(self, _e: ft.ControlEvent) -> None:
        val = (self._path_field.value or "").strip().strip('"').strip("'")
        if val:
            self._update_selection_preview(val)

    def _update_selection_preview(self, path_str: str) -> None:
        p = Path(path_str.strip('"').strip("'"))
        if not p.exists():
            self._preview_info_banner.visible = False
            return

        if p.is_file():
            ext = p.suffix.lower()
            if ext in ARCHIVE_EXTS:
                size_mb = p.stat().st_size / (1024 * 1024)
                self._preview_info_banner.content.controls[1].value = (
                    f"Archivo comprimido: {p.name} ({size_mb:.1f} MB) · Se descomprimirá en carpeta dedicada archive_extracted/"
                )
                self._preview_info_banner.visible = True
            elif ext in SUPPORTED_EXTS:
                size_mb = p.stat().st_size / (1024 * 1024)
                self._preview_info_banner.content.controls[1].value = (
                    f"Imagen individual lista: {p.name} ({size_mb:.2f} MB)"
                )
                self._preview_info_banner.visible = True
            else:
                self._preview_info_banner.visible = False
        elif p.is_dir():
            count = sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS)
            self._preview_info_banner.content.controls[1].value = (
                f"Carpeta en PC: {p.name} · {count} imágenes detectadas listas para análisis"
            )
            self._preview_info_banner.visible = True

        try:
            self._preview_info_banner.update()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # GESTION DE SELECCION Y EVENTOS EN EL PC
    # -------------------------------------------------------------------------
    def _on_mode_change(self, _e: ft.ControlEvent) -> None:
        mode = self._input_mode.value
        self._device_reuse_chk.visible = (mode == "device")
        if mode == "device":
            self._path_field.value = f"Dispositivo ADB ({self._device.serial or 'Activo'})"
            self._path_field.read_only = True
            self._browse_btn.disabled = True
            self._preview_info_banner.visible = False
        else:
            self._path_field.read_only = False
            self._browse_btn.disabled = False
            if self._path_field.value and "Dispositivo ADB" in self._path_field.value:
                self._path_field.value = ""
        try:
            self.view.update()
        except Exception:
            pass

    def _on_browse_click(self, _e: ft.ControlEvent) -> None:
        """Abre el explorador de Windows nativo en la computadora para seleccionar archivos/carpetas."""
        mode = self._input_mode.value
        if mode == "device":
            return

        def _open_native_windows_dialog() -> None:
            chosen_path = None
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                initial = str(Path.home() / "Downloads")

                if mode == "folder":
                    chosen_path = filedialog.askdirectory(
                        title="Seleccionar carpeta con imágenes en el PC",
                        initialdir=initial,
                    )
                elif mode == "archive":
                    chosen_path = filedialog.askopenfilename(
                        title="Seleccionar archivo comprimido (.zip, .rar, .tar, .7z) en el PC",
                        initialdir=initial,
                        filetypes=[
                            ("Archivos comprimidos", "*.zip;*.rar;*.tar;*.7z;*.tgz;*.gz"),
                            ("Todos los archivos", "*.*"),
                        ],
                    )
                else:  # single image
                    chosen_path = filedialog.askopenfilename(
                        title="Seleccionar imagen en el PC",
                        initialdir=initial,
                        filetypes=[
                            ("Archivos de imagen", "*.jpg;*.jpeg;*.png;*.heic;*.heif;*.webp;*.dng;*.tiff;*.bmp"),
                            ("Todos los archivos", "*.*"),
                        ],
                    )
                root.destroy()
            except Exception as ex:
                logger.warning("Fallo al abrir diálogo tkinter: %s", ex)

            if chosen_path:
                self._path_field.value = chosen_path
                self._update_selection_preview(chosen_path)
                try:
                    self.view.update()
                except Exception:
                    pass
            else:
                # Fallback a FilePicker de Flet si tkinter no seleccionó nada
                if mode == "single":
                    self._image_picker.pick_files(
                        dialog_title="Seleccione una imagen en el PC",
                        allowed_extensions=["jpg", "jpeg", "png", "heic", "heif", "webp", "dng", "tiff", "tif", "bmp"],
                    )
                elif mode == "folder":
                    self._folder_picker.get_directory_path(dialog_title="Seleccione la carpeta con imágenes en el PC")
                elif mode == "archive":
                    self._archive_picker.pick_files(
                        dialog_title="Seleccione un archivo comprimido en el PC",
                        allowed_extensions=["zip", "rar", "tar", "7z", "tgz"],
                    )

        threading.Thread(target=_open_native_windows_dialog, daemon=True).start()

    def _on_single_image_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.files and len(e.files) > 0 and e.files[0].path:
            self._path_field.value = e.files[0].path
            self._update_selection_preview(e.files[0].path)
            try:
                self.view.update()
            except Exception:
                pass

    def _on_folder_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.path:
            self._path_field.value = e.path
            self._update_selection_preview(e.path)
            try:
                self.view.update()
            except Exception:
                pass

    def _on_archive_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.files and len(e.files) > 0 and e.files[0].path:
            self._path_field.value = e.files[0].path
            self._update_selection_preview(e.files[0].path)
            try:
                self.view.update()
            except Exception:
                pass

    def _refresh_sessions_dropdown(self) -> None:
        sessions = self._service.list_previous_sessions()
        options = []
        for s in sessions[:15]:
            label = f"{s['session_id']} ({s['total_images']} fotos, {s['gps_images']} con GPS)"
            options.append(ft.dropdown.Option(key=s["path"], text=label))
        self._sessions_dropdown.options = options
        try:
            self._sessions_dropdown.update()
        except Exception:
            pass

    def _on_session_selected(self, _e: ft.ControlEvent) -> None:
        selected_path = self._sessions_dropdown.value
        if not selected_path:
            return
        try:
            self._status_text.value = f"Cargando sesión previa: {selected_path}..."
            self.view.update()
            result = self._service.load_session(selected_path)
            self._render_results(result)
            self._status_text.value = f"Sesión cargada exitosamente: {result.manifest.session_id}"
            self.view.update()
        except Exception as ex:
            self._status_text.value = f"Error al cargar sesión: {ex}"
            self.view.update()

    # -------------------------------------------------------------------------
    # EJECUCION DE ANALISIS FORENSE
    # -------------------------------------------------------------------------
    def _on_start_analysis(self, _e: ft.ControlEvent) -> None:
        if self._is_analyzing:
            return

        mode = self._input_mode.value
        path_val = (self._path_field.value or "").strip().strip('"').strip("'")

        if mode in ("single", "folder", "archive") and not path_val:
            self._status_text.value = "Por favor seleccione una ruta de archivo o carpeta en su computadora antes de iniciar."
            try:
                self.view.update()
            except Exception:
                pass
            return

        self._is_analyzing = True
        self._start_btn.disabled = True
        self._cancel_btn.disabled = False
        self._progress_bar.visible = True
        self._progress_bar.value = 0.05
        self._status_text.value = "Iniciando análisis forense desde la computadora..."
        try:
            self.view.update()
        except Exception:
            pass

        def _worker() -> None:
            try:
                reuse = bool(self._device_reuse_chk.value)
                result = self._service.run_analysis(
                    input_type=mode,
                    input_path=path_val,
                    reuse_backup=reuse,
                    fresh_pull=not reuse,
                )
                self._render_results(result)
                self._status_text.value = "Análisis finalizado con éxito."
                self._refresh_sessions_dropdown()
            except Exception as ex:
                logger.exception("Error en análisis forense de metadatos")
                self._status_text.value = f"Error: {ex}"
            finally:
                self._is_analyzing = False
                self._start_btn.disabled = False
                self._cancel_btn.disabled = True
                self._progress_bar.visible = False
                try:
                    self.page.update()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _on_cancel_analysis(self, _e: ft.ControlEvent) -> None:
        self._service.cancel()
        self._cancel_btn.disabled = True
        self._status_text.value = "Cancelación solicitada..."
        try:
            self.view.update()
        except Exception:
            pass

    def _on_download_tool_click(self, _e: ft.ControlEvent) -> None:
        self._status_text.value = "Descargando ExifTool portable oficial..."
        self._progress_bar.visible = True
        self._progress_bar.value = None
        try:
            self.view.update()
        except Exception:
            pass

        def _worker() -> None:
            try:
                self._service.run_analysis(input_type="download_tool", download_exiftool=True)
                self._status_text.value = "ExifTool instalado con éxito en metadata\\exiftool.exe."
            except Exception as ex:
                self._status_text.value = f"Aviso al descargar ExifTool: {ex}. Se usará el extractor .NET nativo."
            finally:
                self._progress_bar.visible = False
                try:
                    self.page.update()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    # -------------------------------------------------------------------------
    # RENDERIZADO DE RESULTADOS
    # -------------------------------------------------------------------------
    def _render_results(self, result: MetadataSessionResult) -> None:
        self._current_result = result
        summary = result.summary
        manifest = result.manifest

        # Actualizar KPIs
        self._kpi_total.value = str(summary.total_images)
        self._kpi_gps.value = str(summary.gps_images)
        self._kpi_gps_pct.value = f"{summary.gps_coverage_percent:.1f}% del total"
        self._kpi_nogps.value = str(summary.no_gps_images)
        self._kpi_devices.value = str(len(summary.devices))
        self._kpi_dates.value = f"{summary.date_min} a\n{summary.date_max}"

        # Habilitar botones de visor
        self._btn_open_map.disabled = not bool(result.map_html_path and Path(result.map_html_path).exists())
        self._btn_open_dash.disabled = not bool(result.dashboard_html_path and Path(result.dashboard_html_path).exists())
        self._btn_open_folder.disabled = not bool(result.session_dir and Path(result.session_dir).exists())

        # 1. Renderizar Resumen de Dispositivos y Clusters
        self._render_clusters(summary.top_clusters, summary.devices)

        # 2. Renderizar Catálogo de Fotos
        self._render_photos_list()

        # 3. Renderizar Cadena de Custodia
        self._render_evidence_manifest(manifest)

        try:
            self.view.update()
        except Exception:
            pass

    def _render_clusters(self, clusters: List[MetadataCluster], devices: List[Dict[str, Any]]) -> None:
        # Dispositivos
        dev_chips = []
        for dev in devices:
            name = dev.get("Device") or dev.get("Name") or "Desconocido"
            count = dev.get("Count", 0)
            dev_chips.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    bgcolor=theme.ACCENT_LIGHT,
                    border_radius=8,
                    content=ft.Row(
                        spacing=6,
                        controls=[
                            ft.Icon(ft.Icons.PHONE_ANDROID, size=14, color=theme.ACCENT),
                            ft.Text(f"{name}: {count} fotos", size=11, weight=ft.FontWeight.W_600, color=theme.ACCENT),
                        ],
                    ),
                )
            )
        self._devices_summary_container.controls = dev_chips or [ft.Text("No se detectaron dispositivos.", color=theme.TEXT_MUTED)]

        # Clusters
        cluster_cards = []
        for idx, c in enumerate(clusters, 1):
            g_url = c.google_maps_url or f"https://www.google.com/maps/search/?api=1&query={c.lat},{c.lon}"
            cluster_cards.append(
                theme.card(
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=10,
                                controls=[
                                    ft.CircleAvatar(content=ft.Text(str(idx), size=12, weight=ft.FontWeight.BOLD), radius=14, bgcolor=theme.ACCENT_LIGHT),
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Text(f"Coordenadas: {c.lat:.5f}°, {c.lon:.5f}°", size=13, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                            ft.Text(f"Volumen: {c.count} fotografías tomadas en esta ubicación", size=11, color=theme.TEXT_MUTED),
                                        ],
                                    ),
                                ],
                            ),
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.OutlinedButton(
                                        "Ver en Google Maps",
                                        icon=ft.Icons.OPEN_IN_NEW,
                                        on_click=lambda _e, u=g_url: self._open_url(u),
                                    ),
                                    ft.TextButton(
                                        "Filtrar",
                                        icon=ft.Icons.FILTER_LIST,
                                        on_click=lambda _e, lat=c.lat, lon=c.lon: self._filter_by_cluster(lat, lon),
                                    ),
                                ],
                            ),
                        ],
                    ),
                    padding=10,
                )
            )

        self._clusters_container.controls = cluster_cards or [ft.Text("No hay coordenadas GPS en esta colección para generar clusters.", color=theme.TEXT_MUTED)]

    def _render_photos_list(self) -> None:
        if not self._current_result:
            self._photos_list_view.controls = [ft.Text("No hay datos cargados.", color=theme.TEXT_MUTED)]
            return

        records = self._current_result.records
        query = self._search_query.lower()

        filtered = []
        for r in records:
            if self._filter_mode == "gps" and not r.has_gps:
                continue
            if self._filter_mode == "nogps" and r.has_gps:
                continue
            if self._filter_mode == "edited" and not r.software:
                continue

            if query:
                dev_str = f"{r.make} {r.model}".lower()
                if query not in r.file_name.lower() and query not in dev_str and query not in r.date_time_original.lower():
                    continue

            filtered.append(r)

        items = []
        for r in filtered[:200]:  # Limitar vista previa a 200 para fluidez en UI
            gps_label = f"{r.lat:.5f}°, {r.lon:.5f}°" if r.has_gps and r.lat and r.lon else "Sin GPS"
            gps_color = theme.OK if r.has_gps else theme.TEXT_MUTED

            meta_line = f"Fecha: {r.date_time_original or 'Sin fecha'} | Equipo: {r.make} {r.model} | {r.image_width}x{r.image_height}"
            if r.software:
                meta_line += f" | Editor: {r.software}"

            actions = []
            if r.has_gps and r.lat and r.lon:
                g_url = r.google_maps_url or f"https://www.google.com/maps/search/?api=1&query={r.lat},{r.lon}"
                actions.append(
                    ft.IconButton(
                        icon=ft.Icons.LOCATION_ON,
                        tooltip="Abrir ubicación en Google Maps",
                        icon_color=theme.OK,
                        on_click=lambda _e, u=g_url: self._open_url(u),
                    )
                )

            actions.append(
                ft.IconButton(
                    icon=ft.Icons.INFO_OUTLINE,
                    tooltip="Ver todos los metadatos EXIF",
                    on_click=lambda _e, rec=r: self._show_photo_details(rec),
                )
            )

            thumb_control = None
            if r.thumbnail and self._current_result:
                thumb_full = Path(self._current_result.session_dir) / r.thumbnail
                if thumb_full.exists():
                    thumb_control = ft.Image(src=str(thumb_full), width=50, height=50, fit=ft.BoxFit.COVER, border_radius=6)

            if not thumb_control:
                thumb_control = ft.Container(
                    width=50,
                    height=50,
                    border_radius=6,
                    bgcolor=theme.SURFACE_ALT,
                    content=ft.Icon(ft.Icons.IMAGE, size=24, color=theme.TEXT_MUTED),
                )

            items.append(
                theme.card(
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Row(
                                spacing=12,
                                controls=[
                                    thumb_control,
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Text(r.file_name, size=13, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                            ft.Text(meta_line, size=11, color=theme.TEXT_MUTED),
                                            ft.Text(f"Coordenadas: {gps_label}", size=11, color=gps_color),
                                        ],
                                    ),
                                ],
                            ),
                            ft.Row(actions, spacing=4),
                        ],
                    ),
                    padding=8,
                )
            )

        self._photos_list_view.controls = items or [ft.Text("No se encontraron fotografías con los filtros aplicados.", color=theme.TEXT_MUTED)]
        try:
            self._photos_list_view.update()
        except Exception:
            pass

    def _render_evidence_manifest(self, manifest) -> None:
        self._manifest_info_text.value = (
            f"Sesión ID: {manifest.session_id} | Adquisición: {manifest.acquisition_time} | "
            f"Operador: {manifest.host_name}\\{manifest.user} | Dispositivo: {manifest.device_serial} | "
            f"Origen: {manifest.source_type} ({manifest.source_origin})"
        )

        rows = []
        for art in manifest.artifacts:
            kb = art.size_bytes / 1024.0
            rows.append(
                theme.card(
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(art.file_name, size=12, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                    ft.Text(f"Tamaño: {kb:.1f} KB", size=10, color=theme.TEXT_MUTED),
                                ],
                            ),
                            ft.SelectableText(art.sha256, size=11, color=theme.ACCENT),
                        ],
                    ),
                    padding=8,
                )
            )

        self._evidence_artifacts_column.controls = rows or [ft.Text("No hay artefactos registrados en el manifiesto.", color=theme.TEXT_MUTED)]

    # -------------------------------------------------------------------------
    # FILTROS Y ACCIONES SECUNDARIAS
    # -------------------------------------------------------------------------
    def _set_filter(self, mode: str) -> None:
        self._filter_mode = mode
        self._render_photos_list()

    def _on_search_change(self, _e: ft.ControlEvent) -> None:
        self._search_query = self._search_field.value or ""
        self._render_photos_list()

    def _filter_by_cluster(self, lat: float, lon: float) -> None:
        self._sub_tabs.selected_index = 1
        self._search_field.value = f"{lat:.3f}"
        self._search_query = f"{lat:.3f}"
        self._render_photos_list()
        try:
            self.view.update()
        except Exception:
            pass

    def _show_photo_details(self, r: PhotoMetadataRecord) -> None:
        if not self.page:
            return

        dialog_content = ft.Column(
            spacing=6,
            tight=True,
            controls=[
                ft.Text(f"Archivo: {r.file_name}", weight=ft.FontWeight.BOLD),
                ft.Text(f"Ruta: {r.source_file}", size=11, color=theme.TEXT_MUTED),
                ft.Divider(height=1, color=theme.BORDER),
                ft.Text(f"Fecha Original: {r.date_time_original or 'N/A'}"),
                ft.Text(f"Dispositivo: {r.make} {r.model}"),
                ft.Text(f"Coordenadas GPS: {r.lat}, {r.lon}" if r.has_gps else "Sin coordenadas GPS"),
                ft.Text(f"Altitud: {r.altitude} m" if r.altitude is not None else "Altitud: N/A"),
                ft.Text(f"Orientación: {r.orientation}"),
                ft.Text(f"Resolución: {r.image_width} x {r.image_height}"),
                ft.Text(f"ISO: {r.iso} | F: {r.f_number} | Tiempo Exp.: {r.exposure_time}"),
                ft.Text(f"Software: {r.software or 'Ninguno detectado'}"),
                ft.Divider(height=1, color=theme.BORDER),
                ft.Text(f"Hash SHA-256:\n{r.sha256}", size=10, selectable=True),
            ],
        )

        dlg = ft.AlertDialog(
            title=ft.Text("Detalles Forenses de Fotografía"),
            content=dialog_content,
            actions=[
                ft.TextButton("Cerrar", on_click=lambda _e: self._close_dialog(dlg)),
            ],
        )
        self.page.dialog = dlg
        dlg.open = True
        self.page.update()

    def _close_dialog(self, dlg: ft.AlertDialog) -> None:
        dlg.open = False
        if self.page:
            self.page.update()

    def _on_verify_integrity_click(self, _e: ft.ControlEvent) -> None:
        if not self._current_result or not self._current_result.session_dir:
            return
        try:
            verif = self._service.verify_manifest_integrity(self._current_result.session_dir)
            cards = []
            for fname, info in verif.items():
                is_valid = info["valid"]
                color = theme.OK if is_valid else theme.ERR
                badge = "VERIFICADO INTEGRO" if is_valid else "ALTERADO / NO ENCONTRADO"
                cards.append(
                    theme.card(
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Text(fname, size=12, weight=ft.FontWeight.BOLD),
                                        ft.Text(f"SHA-256 Actual: {info['actual_sha256']}", size=10, color=theme.TEXT_MUTED),
                                    ],
                                ),
                                ft.Container(
                                    content=ft.Text(badge, size=10, weight=ft.FontWeight.BOLD, color=color),
                                    bgcolor=theme.OK_LIGHT if is_valid else theme.ERR_LIGHT,
                                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                    border_radius=6,
                                ),
                            ],
                        ),
                        padding=8,
                    )
                )
            self._evidence_artifacts_column.controls = cards
            self.view.update()
        except Exception as ex:
            self._status_text.value = f"Error al verificar integridad: {ex}"
            self.view.update()

    # -------------------------------------------------------------------------
    # ACCIONES DE SERVICIO Y LOGGING
    # -------------------------------------------------------------------------
    def _on_service_log(self, message: str, level: str) -> None:
        if self._on_log:
            self._on_log(f"[{level}] {message}", theme.OK if level == "SUCCESS" else theme.ACCENT)
        logger.info("[%s] %s", level, message)

    def _on_service_progress(self, percent: float, status: str) -> None:
        self._progress_bar.value = percent
        self._status_text.value = status
        try:
            self.page.update()
        except Exception:
            pass

    @staticmethod
    def _open_external_file(path: Optional[str]) -> None:
        if path and Path(path).exists():
            os.startfile(path)  # type: ignore[attr-defined]

    @staticmethod
    def _open_url(url: str) -> None:
        if url:
            os.startfile(url)  # type: ignore[attr-defined]
