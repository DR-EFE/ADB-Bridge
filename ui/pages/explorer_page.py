"""Pestaña «Explorador de Archivos Android»: navegación visual por /sdcard, descarga y subida."""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Callable, List, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService

logger = logging.getLogger("ui.explorer")


class ExplorerPage:
    """Navegador de archivos y carpetas del teléfono."""

    def __init__(self, device: DeviceService, on_log: Optional[Callable[[str, str], None]] = None) -> None:
        self._device = device
        self._on_log = on_log
        self._current_path = "/sdcard"

        self._path_field = ft.TextField(
            value=self._current_path,
            label="Ruta en el teléfono",
            expand=True,
            text_size=13,
            on_submit=self._on_navigate_submit,
        )

        self._files_list = ft.ListView(expand=True, spacing=4)
        self._status_text = ft.Text("Presiona 'Cargar Archivos' para explorar.", size=12, color=theme.TEXT_MUTED)

        # Accesos rápidos
        quick_buttons = [
            ft.TextButton("🏠 /sdcard", on_click=lambda _: self._navigate_to("/sdcard")),
            ft.TextButton("📸 DCIM (Fotos)", on_click=lambda _: self._navigate_to("/sdcard/DCIM/Camera")),
            ft.TextButton("⬇️ Descargas", on_click=lambda _: self._navigate_to("/sdcard/Download")),
            ft.TextButton("📄 Documentos", on_click=lambda _: self._navigate_to("/sdcard/Documents")),
            ft.TextButton("🖼️ Fotos/Pictures", on_click=lambda _: self._navigate_to("/sdcard/Pictures")),
        ]

        self.view = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                theme.card(
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                spacing=8,
                                controls=[
                                    self._path_field,
                                    ft.FilledButton("Ir", icon=ft.Icons.ARROW_FORWARD, on_click=self._on_navigate_submit),
                                    ft.OutlinedButton("⬆ Arriba", icon=ft.Icons.ARROW_UPWARD, on_click=self._navigate_up),
                                    ft.OutlinedButton("🔄 Refrescar", icon=ft.Icons.REFRESH, on_click=lambda _: self._refresh()),
                                ],
                            ),
                            ft.Row(wrap=True, spacing=6, controls=quick_buttons),
                        ],
                    ),
                    padding=12,
                ),
                theme.card(
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.FilledButton("📥 Descargar Fotos Recientes (DCIM)", icon=ft.Icons.PHOTO_CAMERA, on_click=self._download_recent_photos),
                            ft.OutlinedButton("📁 Abrir Carpeta de Descargas en PC", icon=ft.Icons.FOLDER_OPEN, on_click=self._open_downloads_folder),
                            self._status_text,
                        ],
                    ),
                    padding=10,
                ),
                theme.card(self._files_list, expand=True),
            ],
        )

    def _log(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)

    def _get_device_or_alert(self):
        status = self._device.get_status()
        if not status.connected or not self._device.serial:
            msg = status.error or "No hay dispositivo conectado para explorar archivos."
            self._status_text.value = f"[ERROR] {msg}"
            self._status_text.color = theme.ERR
            self._status_text.update()
            self._log(msg, theme.ERR)
            return None
        from ui_automation.adb_manager import get_adb_client
        return get_adb_client().device(self._device.serial)

    def _on_navigate_submit(self, _e: ft.ControlEvent) -> None:
        path = (self._path_field.value or "/sdcard").strip()
        self._navigate_to(path)

    def _navigate_to(self, path: str) -> None:
        self._current_path = path
        self._path_field.value = path
        try:
            self._path_field.update()
        except Exception:
            pass
        self._refresh()

    def _navigate_up(self, _e: ft.ControlEvent) -> None:
        parent = str(Path(self._current_path).parent).replace("\\", "/")
        if parent:
            self._navigate_to(parent)

    def _refresh(self) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return

        self._status_text.value = f"Leyendo {self._current_path}..."
        self._status_text.color = theme.TEXT_MUTED
        try:
            self._status_text.update()
        except Exception:
            pass

        def worker():
            try:
                # Listar con ls -la
                cmd = f"ls -la '{self._current_path}'"
                out = dev.shell(cmd)
                lines = out.splitlines()

                items = []
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("total "):
                        continue

                    parts = line.split(maxsplit=7)
                    if len(parts) < 8:
                        continue

                    perms, _, _, _, size, _, _, name = parts
                    if name in (".", ".."):
                        continue

                    is_dir = perms.startswith("d")
                    icon = ft.Icons.FOLDER if is_dir else ft.Icons.INSERT_DRIVE_FILE_OUTLINED
                    icon_color = theme.ACCENT if is_dir else theme.TEXT_MUTED

                    full_remote_path = f"{self._current_path.rstrip('/')}/{name}"

                    row = ft.Container(
                        content=ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Row(
                                    spacing=10,
                                    controls=[
                                        ft.Icon(icon, color=icon_color, size=18),
                                        ft.Text(name, size=13, weight=ft.FontWeight.W_600 if is_dir else ft.FontWeight.NORMAL),
                                    ],
                                ),
                                ft.Row(
                                    spacing=12,
                                    controls=[
                                        ft.Text(f"{size} B" if not is_dir else "<CARPETA>", size=11, color=theme.TEXT_MUTED),
                                        ft.IconButton(
                                            ft.Icons.DOWNLOAD,
                                            icon_size=16,
                                            tooltip="Descargar al PC",
                                            on_click=lambda _, p=full_remote_path, n=name: self._download_file(p, n),
                                        ) if not is_dir else ft.IconButton(
                                            ft.Icons.ARROW_FORWARD,
                                            icon_size=16,
                                            tooltip="Entrar",
                                            on_click=lambda _, p=full_remote_path: self._navigate_to(p),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                        border_radius=6,
                        bgcolor=theme.SURFACE_ALT,
                    )
                    items.append(row)

                self._files_list.controls = items
                self._status_text.value = f"{len(items)} elementos en {self._current_path}"
                self._status_text.color = theme.OK
                self._files_list.update()
                self._status_text.update()

            except Exception as exc:
                err = f"Error al listar {self._current_path}: {exc}"
                self._status_text.value = err
                self._status_text.color = theme.ERR
                self._status_text.update()
                self._log(err, theme.ERR)

        threading.Thread(target=worker, daemon=True).start()

    def _download_file(self, remote_path: str, filename: str) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return

        def worker():
            try:
                dest_dir = Path("exports/downloads")
                dest_dir.mkdir(parents=True, exist_ok=True)
                local_file = dest_dir / filename
                self._status_text.value = f"Descargando {filename}..."
                self._status_text.update()

                dev.sync.pull(remote_path, str(local_file))
                msg = f"Descargado con éxito: {local_file.resolve()}"
                self._status_text.value = msg
                self._status_text.color = theme.OK
                self._status_text.update()
                self._log(msg, theme.OK)
            except Exception as exc:
                err = f"Fallo al descargar {filename}: {exc}"
                self._status_text.value = err
                self._status_text.color = theme.ERR
                self._status_text.update()
                self._log(err, theme.ERR)

        threading.Thread(target=worker, daemon=True).start()

    def _download_recent_photos(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return

        def worker():
            try:
                self._status_text.value = "Buscando fotos recientes en DCIM/Camera..."
                self._status_text.update()
                out = dev.shell("ls -t /sdcard/DCIM/Camera 2>/dev/null | head -n 10")
                files = [f.strip() for f in out.splitlines() if f.strip()]
                if not files:
                    self._status_text.value = "No se encontraron fotos en /sdcard/DCIM/Camera."
                    self._status_text.update()
                    return

                dest_dir = Path("exports/fotos_recientes")
                dest_dir.mkdir(parents=True, exist_ok=True)
                count = 0
                for fname in files:
                    rem = f"/sdcard/DCIM/Camera/{fname}"
                    loc = dest_dir / fname
                    dev.sync.pull(rem, str(loc))
                    count += 1

                msg = f"¡{count} fotos descargadas en {dest_dir.resolve()}!"
                self._status_text.value = msg
                self._status_text.color = theme.OK
                self._status_text.update()
                self._log(msg, theme.OK)
                os.startfile(str(dest_dir.resolve()))
            except Exception as exc:
                err = f"Error al descargar fotos: {exc}"
                self._status_text.value = err
                self._status_text.color = theme.ERR
                self._status_text.update()
                self._log(err, theme.ERR)

        threading.Thread(target=worker, daemon=True).start()

    def _open_downloads_folder(self, _e: ft.ControlEvent) -> None:
        dest_dir = Path("exports").resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(dest_dir))

    def shutdown(self) -> None:
        pass
