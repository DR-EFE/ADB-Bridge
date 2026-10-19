"""Pestaña «Explorador de Archivos y Transferencias»: navegación visual, subida, descarga, gestión y cola de transferencias."""

from __future__ import annotations

import logging
import os
import re
import shlex
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService
from ..services.transfer_service import RemotePath, TransferJob, TransferService, TransferStatus, TransferType

logger = logging.getLogger("ui.explorer")


class ExplorerPage:
    """Navegador de archivos y gestor de transferencias ADB sin bloquear la interfaz."""

    def __init__(
        self,
        device: DeviceService,
        transfer_service: Optional[TransferService] = None,
        page: Optional[ft.Page] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._device = device
        self.page = page
        self._on_log = on_log
        self._current_path = "/sdcard"
        self._is_shutdown = False
        self._refresh_generation = 0
        # La conexión Wi-Fi puede cambiar de puerto mientras esta pestaña está
        # abierta. Al recibir el evento se vuelve a listar usando el serial
        # actual, sin obligar al usuario a cerrar o reiniciar la aplicación.
        self._device.add_device_change_listener(self._on_device_changed)

        # Servicio de transferencias compartido o propio
        self._transfer_service = transfer_service or TransferService(
            reconnect_callback=self._device.reconnect_device,
            on_job_updated=self._on_transfer_job_updated,
        )

        # Estado para descargas pendientes de selección de carpeta
        self._pending_download_remote_paths: List[str] = []

        # FilePickers nativos Flet
        self._file_picker = ft.FilePicker(on_result=self._on_files_selected_for_upload)
        self._folder_picker = ft.FilePicker(on_result=self._on_folder_selected_for_upload)
        self._download_dir_picker = ft.FilePicker(on_result=self._on_download_dir_selected)

        if self.page:
            self.page.services.extend([self._file_picker, self._folder_picker, self._download_dir_picker])

        # Barra de navegación
        self._path_field = ft.TextField(
            value=self._current_path,
            label="Ruta remota (/sdcard)",
            expand=True,
            text_size=13,
            on_submit=self._on_navigate_submit,
        )

        self._files_list = ft.ListView(expand=True, spacing=4)
        self._status_text = ft.Text("Presiona 'Ir' o 'Refrescar' para explorar.", size=12, color=theme.TEXT_MUTED)
        self._storage_text = ft.Text("Almacenamiento: pendiente de consulta", size=11, color=theme.TEXT_MUTED)
        self._breadcrumbs = ft.Row(wrap=True, spacing=2)

        # Accesos rápidos estándar
        quick_buttons = [
            ft.TextButton("/sdcard", icon=ft.Icons.HOME, on_click=lambda _: self._navigate_to("/sdcard")),
            ft.TextButton("DCIM", icon=ft.Icons.CAMERA_ALT, on_click=lambda _: self._navigate_to("/sdcard/DCIM")),
            ft.TextButton("Download", icon=ft.Icons.DOWNLOAD, on_click=lambda _: self._navigate_to("/sdcard/Download")),
            ft.TextButton("Documents", icon=ft.Icons.DESCRIPTION, on_click=lambda _: self._navigate_to("/sdcard/Documents")),
            ft.TextButton("Pictures", icon=ft.Icons.IMAGE, on_click=lambda _: self._navigate_to("/sdcard/Pictures")),
            ft.TextButton("Music", icon=ft.Icons.MUSIC_NOTE, on_click=lambda _: self._navigate_to("/sdcard/Music")),
        ]
        self._quick_locations = ft.Row(wrap=True, spacing=4, controls=quick_buttons)

        # Zona visual "Agregar archivos o carpetas" (acceso claro a selectores nativos)
        self._drop_zone = theme.card(
            ft.Container(
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6,
                    controls=[
                        ft.Icon(ft.Icons.CLOUD_UPLOAD_OUTLINED, size=32, color=theme.ACCENT),
                        ft.Text("Zona para agregar archivos o carpetas", weight=ft.FontWeight.BOLD, size=13),
                        ft.Text(
                            "Selecciona archivos individuales o carpetas completas del PC para subirlos a la ruta actual.",
                            size=11,
                            color=theme.TEXT_MUTED,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Row(
                            alignment=ft.MainAxisAlignment.CENTER,
                            spacing=10,
                            controls=[
                                ft.FilledButton(
                                    "Subir Archivos",
                                    icon=ft.Icons.ATTACH_FILE,
                                    on_click=self._on_upload_files_click,
                                ),
                                ft.OutlinedButton(
                                    "Subir Carpeta",
                                    icon=ft.Icons.DRIVE_FOLDER_UPLOAD,
                                    on_click=self._on_upload_folder_click,
                                ),
                            ],
                        ),
                    ],
                ),
                padding=12,
            )
        )

        # Panel de Transferencias
        self._transfers_column = ft.ListView(expand=True, spacing=6)
        self._transfers_summary = ft.Text("0 transferencias activas", size=12, color=theme.TEXT_MUTED)

        # Ensamblar vista
        self.view = ft.Row(
            expand=True,
            spacing=10,
            controls=[
                # Columna Izquierda: Explorador de Archivos
                ft.Column(
                    expand=3,
                    spacing=8,
                    controls=[
                        theme.card(
                            ft.Column(
                                spacing=6,
                                controls=[
                                    ft.Row(
                                        spacing=6,
                                        controls=[
                                            self._path_field,
                                            ft.FilledButton("Ir", icon=ft.Icons.ARROW_FORWARD, on_click=self._on_navigate_submit),
                                            ft.OutlinedButton("Arriba", icon=ft.Icons.ARROW_UPWARD, on_click=self._navigate_up),
                                            ft.OutlinedButton("Refrescar", icon=ft.Icons.REFRESH, tooltip="Refrescar", on_click=lambda _: self._refresh()),
                                            ft.FilledButton(
                                                "Nueva Carpeta",
                                                icon=ft.Icons.CREATE_NEW_FOLDER,
                                                on_click=lambda _: self._show_create_folder_dialog(),
                                            ),
                                        ],
                                    ),
                                    self._breadcrumbs,
                                    self._quick_locations,
                                ],
                            ),
                            padding=8,
                        ),
                        self._drop_zone,
                        theme.card(
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Column(spacing=2, expand=True, controls=[self._status_text, self._storage_text]),
                                    ft.OutlinedButton(
                                        "Abrir Descargas Locales",
                                        icon=ft.Icons.FOLDER_OPEN,
                                        on_click=self._open_downloads_folder,
                                    ),
                                ],
                            ),
                            padding=6,
                        ),
                        theme.card(self._files_list, expand=True),
                    ],
                ),
                # Columna Derecha: Panel de Transferencias
                theme.card(
                    ft.Column(
                        expand=True,
                        spacing=8,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Row(
                                        spacing=6,
                                        controls=[
                                            ft.Icon(ft.Icons.SWAP_VERT_ROUNDED, color=theme.ACCENT, size=20),
                                            ft.Text("Transferencias", weight=ft.FontWeight.BOLD, size=14),
                                        ],
                                    ),
                                    ft.TextButton(
                                        "Limpiar terminadas",
                                        on_click=lambda _: self._clear_completed_transfers(),
                                    ),
                                ],
                            ),
                            self._transfers_summary,
                            ft.Divider(height=1, color=theme.BORDER),
                            self._transfers_column,
                        ],
                    ),
                    padding=10,
                    expand=2,
                ),
            ],
        )
        self._render_breadcrumbs()

    def _log(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)

    def _check_device_ready(self) -> Optional[str]:
        status = self._device.get_status()
        if status.requires_selection:
            return "Hay varios dispositivos conectados. Selecciona uno en el panel lateral."
        if not status.connected or not self._device.serial:
            return status.error or "No hay dispositivo ADB conectado."
        return None

    def _on_device_changed(self) -> None:
        if self._is_shutdown:
            return
        logger.info("El Explorador recibió un cambio de dispositivo; actualizando %s", self._current_path)
        self._refresh()

    def _on_navigate_submit(self, _e: ft.ControlEvent) -> None:
        path = (self._path_field.value or "/sdcard").strip()
        self._navigate_to(path)

    @staticmethod
    def _format_bytes(value: str) -> str:
        """Convierte tamaños del listado remoto a una unidad legible."""
        try:
            size = int(value)
        except (TypeError, ValueError):
            return "—"
        units = ("B", "KB", "MB", "GB", "TB")
        amount = float(size)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
            amount /= 1024
        return f"{size} B"

    @staticmethod
    def _format_storage(df_output: str) -> str:
        """Resume ``df -k`` sin depender del nombre del volumen Android."""
        rows = [line.split() for line in (df_output or "").splitlines() if line.split()]
        if len(rows) < 2:
            return "Almacenamiento: no disponible"
        row = rows[-1]
        if len(row) < 5:
            return "Almacenamiento: no disponible"
        try:
            total_kb, used_kb, available_kb = (int(row[1]), int(row[2]), int(row[3]))
            return (
                f"Almacenamiento: {ExplorerPage._format_bytes(str(available_kb * 1024))} libres de "
                f"{ExplorerPage._format_bytes(str(total_kb * 1024))} "
                f"({ExplorerPage._format_bytes(str(used_kb * 1024))} usados)"
            )
        except (TypeError, ValueError, IndexError):
            return "Almacenamiento: no disponible"

    @staticmethod
    def _validate_remote_name(value: str) -> Optional[str]:
        name = value.strip()
        if not name:
            return "Indica un nombre."
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            return "El nombre no puede contener barras ni usar . o ..."
        return None

    @staticmethod
    def _friendly_remote_error(raw_error: str, path: str, serial: str) -> str:
        """Traduce respuestas frecuentes de ADB a acciones comprensibles."""
        message = (raw_error or "").strip()
        lowered = message.lower()
        if "device offline" in lowered or "device not found" in lowered or "no devices" in lowered:
            return f"Conexión perdida con {serial}. Espera la reconexión Wi‑Fi y pulsa Refrescar."
        if "permission denied" in lowered or "not allowed" in lowered:
            return f"Android no permite abrir {path}. Usa una carpeta de almacenamiento compartido como /sdcard/Download."
        if "no such file" in lowered or "not found" in lowered:
            return f"La ruta {path} ya no existe o fue movida. Regresa a la carpeta anterior."
        if "read-only" in lowered:
            return f"{path} es de solo lectura; no se pueden subir, renombrar ni eliminar elementos ahí."
        return f"No se pudo abrir {path}: {message or 'respuesta no válida de ADB'}"

    def _render_breadcrumbs(self) -> None:
        parts = [part for part in self._current_path.split("/") if part]
        controls: List[ft.Control] = [
            ft.TextButton("Raíz", icon=ft.Icons.HOME, on_click=lambda _: self._navigate_to("/")),
        ]
        accumulated = ""
        for part in parts:
            accumulated += "/" + part
            controls.append(ft.Text("›", color=theme.TEXT_MUTED, size=12))
            controls.append(ft.TextButton(part, on_click=lambda _, p=accumulated: self._navigate_to(p)))
        self._breadcrumbs.controls = controls

    def _update_quick_locations(self, existing_paths: List[str]) -> None:
        """Muestra solo ubicaciones que el dispositivo expone realmente."""
        known_locations = [
            ("/sdcard", "Almacenamiento", ft.Icons.PHONE_ANDROID),
            ("/sdcard/DCIM", "Cámara", ft.Icons.CAMERA_ALT),
            ("/sdcard/Download", "Descargas", ft.Icons.DOWNLOAD),
            ("/sdcard/Documents", "Documentos", ft.Icons.DESCRIPTION),
            ("/sdcard/Pictures", "Imágenes", ft.Icons.IMAGE),
            ("/sdcard/Music", "Música", ft.Icons.MUSIC_NOTE),
            ("/sdcard/Android/media", "Media de apps", ft.Icons.APPS),
        ]
        allowed = set(existing_paths)
        self._quick_locations.controls = [
            ft.TextButton(label, icon=icon, on_click=lambda _, p=path: self._navigate_to(p))
            for path, label, icon in known_locations
            if path in allowed
        ]

    @staticmethod
    def _parse_ls_output(output: str) -> List[Dict[str, str]]:
        """Interpreta ``ls -lan`` preservando nombres con espacios y enlaces."""
        entries: List[Dict[str, str]] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("total "):
                continue
            parts = line.split(maxsplit=7)
            if len(parts) < 8 or not re.match(r"^[bcdlps-]", parts[0]):
                continue
            name = parts[7]
            if " -> " in name and parts[0].startswith("l"):
                name = name.split(" -> ", 1)[0]
            if name in (".", ".."):
                continue
            entries.append({"perms": parts[0], "size": parts[4], "name": name})
        return sorted(entries, key=lambda item: (not item["perms"].startswith("d"), item["name"].casefold()))

    def _navigate_to(self, path: str) -> None:
        clean = RemotePath(path).clean_path
        self._current_path = clean
        self._path_field.value = clean
        self._render_breadcrumbs()
        try:
            self._path_field.update()
        except Exception:
            pass
        self._refresh()

    def _navigate_up(self, _e: ft.ControlEvent) -> None:
        parent = RemotePath(self._current_path).parent
        if parent:
            self._navigate_to(parent)

    def _refresh(self) -> None:
        err = self._check_device_ready()
        if err:
            self._status_text.value = f"[ERROR] {err}"
            self._status_text.color = theme.ERR
            try:
                self._status_text.update()
            except Exception:
                pass
            return

        self._status_text.value = f"Leyendo {self._current_path}..."
        self._status_text.color = theme.TEXT_MUTED
        try:
            self._status_text.update()
        except Exception:
            pass

        serial = self._device.serial
        path = self._current_path
        self._refresh_generation += 1
        generation = self._refresh_generation

        def worker():
            try:
                from ui_automation.adb_manager import get_adb_client
                client = get_adb_client()
                dev = client.device(serial)

                # ``--`` y shlex.quote hacen que una carpeta con espacios, comillas
                # o nombres similares a opciones se consulte de forma segura.
                cmd = f"ls -lan -- {shlex.quote(path)} 2>&1; printf '\n__ANDROID_TOOLS_EXIT:%s\n' $?"
                out = dev.shell(cmd)
                exit_match = re.search(r"__ANDROID_TOOLS_EXIT:(\d+)", out)
                exit_code = int(exit_match.group(1)) if exit_match else 0
                listing = out[:exit_match.start()] if exit_match else out
                if exit_code != 0:
                    raise RuntimeError(listing.strip() or f"ls terminó con código {exit_code}")

                # Consultas ligeras: espacio disponible y ubicaciones que sí
                # existen en este Android (varía por fabricante/versión).
                df_out = dev.shell(f"df -k {shlex.quote(path)} 2>/dev/null || true")
                roots_out = dev.shell(
                    "for p in /sdcard /sdcard/DCIM /sdcard/Download /sdcard/Documents "
                    "/sdcard/Pictures /sdcard/Music /sdcard/Android/media; do "
                    "[ -d \"$p\" ] && printf '%s\\n' \"$p\"; done"
                )
                entries = self._parse_ls_output(listing)
                if generation != self._refresh_generation or serial != self._device.serial or path != self._current_path:
                    return

                items = []
                for entry in entries:
                    perms, size, name = entry["perms"], entry["size"], entry["name"]
                    is_dir = perms.startswith("d")
                    full_remote_path = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"

                    icon = ft.Icons.FOLDER if is_dir else ft.Icons.INSERT_DRIVE_FILE_OUTLINED
                    icon_color = theme.ACCENT if is_dir else theme.TEXT_MUTED

                    # Fila de elemento remoto
                    row = ft.Container(
                        content=ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Icon(icon, color=icon_color, size=18),
                                        ft.Text(
                                            name,
                                            size=13,
                                            weight=ft.FontWeight.W_600 if is_dir else ft.FontWeight.NORMAL,
                                        ),
                                    ],
                                ),
                                ft.Row(
                                    spacing=4,
                                    controls=[
                                        ft.Text(self._format_bytes(size) if not is_dir else "Carpeta", size=11, color=theme.TEXT_MUTED),
                                        # Botón entrar si es dir
                                        ft.IconButton(
                                            ft.Icons.ARROW_FORWARD,
                                            icon_size=16,
                                            tooltip="Abrir carpeta",
                                            on_click=lambda _, p=full_remote_path: self._navigate_to(p),
                                        ) if is_dir else ft.Container(),
                                        # Botón descargar rápido a PC
                                        ft.IconButton(
                                            ft.Icons.DOWNLOAD,
                                            icon_size=16,
                                            tooltip="Descargar a PC (exports/downloads)",
                                            on_click=lambda _, p=full_remote_path: self._prompt_download(p),
                                        ),
                                        # Botón descargar a carpeta personalizada
                                        ft.IconButton(
                                            ft.Icons.DRIVE_FILE_MOVE_OUTLINED,
                                            icon_size=16,
                                            tooltip="Descargar y elegir carpeta...",
                                            on_click=lambda _, p=full_remote_path: self._download_to_custom_dir(p),
                                        ),
                                        # Botón renombrar
                                        ft.IconButton(
                                            ft.Icons.EDIT_OUTLINED,
                                            icon_size=16,
                                            tooltip="Renombrar",
                                            on_click=lambda _, p=full_remote_path, n=name: self._show_rename_dialog(p, n),
                                        ),
                                        # Botón eliminar
                                        ft.IconButton(
                                            ft.Icons.DELETE_OUTLINE,
                                            icon_size=16,
                                            tooltip="Eliminar",
                                            icon_color=theme.ERR,
                                            on_click=lambda _, p=full_remote_path, d=is_dir: self._show_delete_dialog(p, d),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        border_radius=4,
                        bgcolor=theme.SURFACE_ALT,
                    )
                    items.append(row)

                self._files_list.controls = items or [
                    ft.Container(ft.Text("Esta carpeta está vacía.", color=theme.TEXT_MUTED), padding=16)
                ]
                self._status_text.value = f"{len(items)} elementos en {path}"
                self._status_text.color = theme.OK
                self._storage_text.value = self._format_storage(df_out)
                self._update_quick_locations([line.strip() for line in roots_out.splitlines() if line.strip()])
                self._render_breadcrumbs()
                try:
                    self._files_list.update()
                    self._status_text.update()
                    self._storage_text.update()
                    self._quick_locations.update()
                    self._breadcrumbs.update()
                except Exception:
                    pass

            except Exception as exc:
                if generation != self._refresh_generation:
                    return
                err_msg = self._friendly_remote_error(str(exc), path, serial or "dispositivo")
                self._status_text.value = err_msg
                self._status_text.color = theme.ERR
                try:
                    self._status_text.update()
                except Exception:
                    pass
                self._log(err_msg, theme.ERR)

        threading.Thread(target=worker, name="explorer-list-worker", daemon=True).start()

    # -------------------------------------------------------------------------
    # Diálogos con confirmación y ruta remota completa
    # -------------------------------------------------------------------------
    def _show_create_folder_dialog(self) -> None:
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return

        name_field = ft.TextField(label="Nombre de la carpeta", autofocus=True)
        dest_display = ft.Text(f"Ruta remota destino: {self._current_path}/", size=12, color=theme.TEXT_MUTED)

        def on_confirm(_e: ft.ControlEvent) -> None:
            folder_name = (name_field.value or "").strip()
            invalid = self._validate_remote_name(folder_name)
            if invalid:
                name_field.error_text = invalid
                name_field.update()
                return
            new_full_path = f"{self._current_path.rstrip('/')}/{folder_name}"
            dialog.open = False
            self.page.update()

            self._transfer_service.enqueue_mkdir(self._device.serial, new_full_path)
            self._log(f"Encolada creación de carpeta: {new_full_path}", theme.INFO)
            # Refrescar después de un breve momento
            threading.Timer(0.8, self._refresh).start()

        def on_cancel(_e: ft.ControlEvent) -> None:
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Crear nueva carpeta"),
            content=ft.Column(spacing=8, tight=True, controls=[dest_display, name_field]),
            actions=[
                ft.TextButton("Cancelar", on_click=on_cancel),
                ft.FilledButton("Crear", on_click=on_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        if self.page:
            self.page.overlay.append(dialog)
            dialog.open = True
            self.page.update()

    def _show_rename_dialog(self, remote_path: str, current_name: str) -> None:
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return

        name_field = ft.TextField(value=current_name, label="Nuevo nombre", autofocus=True)
        curr_display = ft.Text(f"Ruta remota actual:\n{remote_path}", size=11, color=theme.TEXT_MUTED)

        def on_confirm(_e: ft.ControlEvent) -> None:
            new_name = (name_field.value or "").strip()
            if not new_name or new_name == current_name:
                dialog.open = False
                self.page.update()
                return
            invalid = self._validate_remote_name(new_name)
            if invalid:
                name_field.error_text = invalid
                name_field.update()
                return

            parent = RemotePath(remote_path).parent
            new_path = f"{parent.rstrip('/')}/{new_name}"
            dialog.open = False
            self.page.update()

            self._transfer_service.enqueue_rename(self._device.serial, remote_path, new_path)
            self._log(f"Encolado renombrado: {remote_path} -> {new_path}", theme.INFO)
            threading.Timer(0.8, self._refresh).start()

        def on_cancel(_e: ft.ControlEvent) -> None:
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Renombrar elemento"),
            content=ft.Column(spacing=8, tight=True, controls=[curr_display, name_field]),
            actions=[
                ft.TextButton("Cancelar", on_click=on_cancel),
                ft.FilledButton("Renombrar", on_click=on_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        if self.page:
            self.page.overlay.append(dialog)
            dialog.open = True
            self.page.update()

    def _show_delete_dialog(self, remote_path: str, is_dir: bool) -> None:
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return

        def on_confirm(_e: ft.ControlEvent) -> None:
            dialog.open = False
            self.page.update()

            self._transfer_service.enqueue_delete(self._device.serial, remote_path)
            self._log(f"Encolada eliminación: {remote_path}", theme.WARN)
            threading.Timer(0.8, self._refresh).start()

        def on_cancel(_e: ft.ControlEvent) -> None:
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Confirmar eliminación"),
            content=ft.Column(
                spacing=8,
                tight=True,
                controls=[
                    ft.Text(f"¿Estás seguro de que deseas eliminar permanentemente {'esta carpeta y todo su contenido' if is_dir else 'este archivo'}?"),
                    ft.Text(f"Ruta remota completa:\n{remote_path}", weight=ft.FontWeight.BOLD, size=12, color=theme.ERR),
                ],
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=on_cancel),
                ft.FilledButton("Eliminar definitivamente", style=ft.ButtonStyle(bgcolor=theme.ERR), on_click=on_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        if self.page:
            self.page.overlay.append(dialog)
            dialog.open = True
            self.page.update()

    # -------------------------------------------------------------------------
    # Operaciones de subida y descarga nativas
    # -------------------------------------------------------------------------
    def _on_files_selected_for_upload(self, e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return

        local_files = [f.path for f in e.files if f.path]
        if local_files:
            job_ids = self._transfer_service.enqueue_push(self._device.serial, local_files, self._current_path)
            self._log(f"Encolada subida de {len(job_ids)} archivos hacia {self._current_path}", theme.OK)
            self._render_transfers_panel()

    def _on_folder_selected_for_upload(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return

        job_ids = self._transfer_service.enqueue_push(self._device.serial, [e.path], self._current_path)
        self._log(f"Encolada subida de carpeta {Path(e.path).name} hacia {self._current_path}", theme.OK)
        self._render_transfers_panel()

    async def _on_upload_files_click(self, _e: ft.ControlEvent) -> None:
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return
        files = await self._file_picker.pick_files(
            allow_multiple=True,
            dialog_title="Seleccionar archivos locales para subir a Android",
        )
        if files:
            local_files = [f.path for f in files if f.path]
            if local_files:
                job_ids = self._transfer_service.enqueue_push(self._device.serial, local_files, self._current_path)
                self._log(f"Encolada subida de {len(job_ids)} archivos hacia {self._current_path}", theme.OK)
                self._render_transfers_panel()

    async def _on_upload_folder_click(self, _e: ft.ControlEvent) -> None:
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return
        folder = await self._folder_picker.get_directory_path(
            dialog_title="Seleccionar carpeta local para subir a Android (conserva estructura)",
        )
        if folder:
            job_ids = self._transfer_service.enqueue_push(self._device.serial, [folder], self._current_path)
            self._log(f"Encolada subida de carpeta {Path(folder).name} hacia {self._current_path}", theme.OK)
            self._render_transfers_panel()

    def _prompt_download(self, remote_path: str, custom_dir: Optional[str] = None) -> None:
        err = self._check_device_ready()
        if err:
            self._log(err, theme.ERR)
            return

        target_dir = Path(custom_dir or "exports/downloads").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        name = RemotePath(remote_path).name or remote_path

        self._transfer_service.enqueue_pull(self._device.serial, [remote_path], str(target_dir))
        self._log(f"Iniciada descarga de '{name}' hacia {target_dir}", theme.OK)
        self._status_text.value = f"Descargando {name}..."
        self._status_text.color = theme.OK
        try:
            self._status_text.update()
        except Exception:
            pass

        self._render_transfers_panel()

        if self.page:
            try:
                snack = ft.SnackBar(
                    content=ft.Text(f"Descargando '{name}' hacia exports/downloads"),
                    action="Abrir descargas",
                    on_action=self._open_downloads_folder,
                    duration=4000,
                )
                self.page.overlay.append(snack)
                snack.open = True
                self.page.update()
            except Exception:
                pass

    def _download_to_custom_dir(self, remote_path: str) -> None:
        async def _worker():
            dest = await self._download_dir_picker.get_directory_path(
                dialog_title="Seleccionar carpeta de destino en tu PC para la descarga"
            )
            if dest:
                self._prompt_download(remote_path, custom_dir=dest)

        if self.page:
            self.page.run_task(_worker)
        else:
            self._prompt_download(remote_path)

    def _on_download_dir_selected(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path or not self._pending_download_remote_paths:
            return
        dest_dir = e.path
        for rp in self._pending_download_remote_paths:
            self._prompt_download(rp, custom_dir=dest_dir)
        self._pending_download_remote_paths = []

    def _open_downloads_folder(self, _e: ft.ControlEvent) -> None:
        dest_dir = Path("exports/downloads").resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(dest_dir))

    # -------------------------------------------------------------------------
    # Panel de Transferencias en tiempo real
    # -------------------------------------------------------------------------
    def _on_transfer_job_updated(self, job: TransferJob) -> None:
        self._render_transfers_panel()

    def _clear_completed_transfers(self) -> None:
        self._transfer_service.clear_completed_jobs()
        self._render_transfers_panel()

    def _render_transfers_panel(self) -> None:
        jobs = self._transfer_service.get_jobs()
        active = sum(1 for j in jobs if j.status in (TransferStatus.QUEUED, TransferStatus.RUNNING))
        completed = sum(1 for j in jobs if j.status == TransferStatus.COMPLETED)
        failed = sum(1 for j in jobs if j.status in (TransferStatus.FAILED, TransferStatus.PARTIAL))

        self._transfers_summary.value = f"{active} activas | {completed} completadas | {failed} fallidas/parciales"

        job_cards = []
        for job in reversed(jobs):
            # Estado badge
            status_color = theme.ACCENT
            if job.status == TransferStatus.COMPLETED:
                status_color = theme.OK
            elif job.status in (TransferStatus.FAILED, TransferStatus.PARTIAL):
                status_color = theme.ERR if job.status == TransferStatus.FAILED else theme.WARN
            elif job.status == TransferStatus.CANCELLED:
                status_color = theme.TEXT_MUTED

            icon = ft.Icons.ARROW_UPWARD if "PUSH" in job.job_type else ft.Icons.ARROW_DOWNWARD
            if job.job_type == TransferType.MKDIR:
                icon = ft.Icons.CREATE_NEW_FOLDER
            elif job.job_type == TransferType.RENAME:
                icon = ft.Icons.EDIT
            elif job.job_type == TransferType.DELETE:
                icon = ft.Icons.DELETE

            # Acciones según estado
            actions = []
            if job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING):
                actions.append(
                    ft.TextButton(
                        "Cancelar",
                        on_click=lambda _, jid=job.id: self._transfer_service.cancel_job(jid),
                    )
                )
            elif job.status in (TransferStatus.FAILED, TransferStatus.CANCELLED, TransferStatus.PARTIAL):
                actions.append(
                    ft.TextButton(
                        "Reintentar",
                        on_click=lambda _, jid=job.id: self._transfer_service.retry_job(jid),
                    )
                )

            detail_line = ""
            if job.current_file:
                detail_line = f"Archivo: {job.current_file} ({job.files_transferred}/{job.total_files})"
            if job.error:
                detail_line = f"Error: {job.error}"

            card = theme.card(
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        ft.Icon(icon, size=16, color=status_color),
                                        ft.Text(job.job_type.value, size=11, weight=ft.FontWeight.BOLD),
                                    ],
                                ),
                                ft.Container(
                                    content=ft.Text(job.status.value, size=10, weight=ft.FontWeight.BOLD, color=status_color),
                                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                    border_radius=4,
                                    bgcolor=theme.SURFACE_ALT,
                                ),
                            ],
                        ),
                        ft.Text(
                            f"{RemotePath(job.source).name or job.source} -> {RemotePath(job.destination).name or job.destination}",
                            size=12,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.ProgressBar(value=job.progress, color=status_color),
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(detail_line, size=10, color=theme.TEXT_MUTED, expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Row(spacing=4, controls=actions),
                            ],
                        ),
                    ],
                ),
                padding=6,
            )
            job_cards.append(card)

        self._transfers_column.controls = job_cards
        try:
            self._transfers_summary.update()
            self._transfers_column.update()
        except Exception:
            pass

    def shutdown(self) -> None:
        """Detiene transferencias al cerrar."""
        self._is_shutdown = True
        self._device.remove_device_change_listener(self._on_device_changed)
        self._transfer_service.shutdown()
