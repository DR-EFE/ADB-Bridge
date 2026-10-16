"""Pestaña «WhatsApp Extractor»: exporta los N chats más recientes en un hilo secundario."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import flet as ft

from ui_automation.device import DeviceManager
from ui_automation.whatsapp import (
    WhatsAppClient,
    install_receiver,
    uninstall_receiver,
)

from .. import theme
from ..components.log_view import LogView, UiLogHandler, level_color
from ..services.device_service import DeviceService
from ..tasks import BackgroundTask

logger = logging.getLogger("ui.whatsapp")

LOG_PREFIXES = ("ui_automation.whatsapp", "ui.whatsapp")
# chats.py loguea: "[3/10] Iniciando exportación de 'X'..."
_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\] Iniciando exportación")


class WhatsAppPage:
    """Contenido de la pestaña. El control a insertar es ``.view``."""

    def __init__(self, device: DeviceService) -> None:
        self._device = device
        self._task: Optional[BackgroundTask] = None
        self._receiver_task: Optional[BackgroundTask] = None
        self._log_handler: Optional[UiLogHandler] = None
        self._results: List[Dict[str, Any]] = []

        self._count = ft.TextField(label="Cantidad de chats", value="10", width=170,
                                   keyboard_type=ft.KeyboardType.NUMBER)
        self._media = ft.Switch(label="Incluir multimedia (.zip)", value=False)
        self._out_dir = ft.TextField(label="Carpeta de destino", value="./exports", width=260)
        self._export_btn = ft.FilledButton("Exportar", icon=ft.Icons.DOWNLOAD, on_click=self._on_export)
        self._install_receiver_btn = ft.OutlinedButton(
            "Instalar auxiliar", icon=ft.Icons.INSTALL_MOBILE, on_click=self._on_install_receiver
        )
        self._remove_receiver_btn = ft.OutlinedButton(
            "Quitar auxiliar", icon=ft.Icons.DELETE_OUTLINE, on_click=self._on_remove_receiver
        )
        self._receiver_status = ft.Text("Auxiliar de archivos: estado desconocido", size=11, color=theme.TEXT_MUTED)
        self._progress = ft.ProgressBar(value=0, bar_height=8, border_radius=4, color=theme.ACCENT, bgcolor=theme.BORDER)
        self._progress_text = ft.Text("Listo.", size=12, color=theme.TEXT_MUTED)
        self._log = LogView()

        self.view = ft.Column(
            expand=True,
            spacing=14,
            controls=[
                theme.card(
                    ft.Row(
                        wrap=True, spacing=24, run_spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._count, self._media, self._out_dir, self._export_btn,
                            self._install_receiver_btn, self._remove_receiver_btn,
                            self._receiver_status,
                        ],
                    )
                ),
                theme.card(ft.Column([self._progress, self._progress_text], spacing=8)),
                ft.Text("Registro de estado", size=14, weight=ft.FontWeight.W_600),
                self._log.view,
            ],
        )

    # ---------------------------------------------------------- acciones

    def shutdown(self) -> None:
        """La exportación no es cancelable en esta fase; solo limpia el handler."""
        self._detach_log_handler()

    def _on_install_receiver(self, _e: ft.ControlEvent) -> None:
        serial = self._device.serial
        self._install_receiver_btn.disabled = True
        self._receiver_status.value = "Instalando auxiliar en el teléfono..."
        self._receiver_status.update()

        def work(_stop) -> None:
            install_receiver(serial)

        def done(error: Optional[BaseException]) -> None:
            self._install_receiver_btn.disabled = False
            if error:
                self._receiver_status.value = f"No se pudo instalar: {error}"
                self._receiver_status.color = theme.ERR
            else:
                self._receiver_status.value = "Auxiliar instalado: disponible en Compartir → Más"
                self._receiver_status.color = theme.OK
            self._receiver_status.update()
            self.view.update()

        self._receiver_task = BackgroundTask("whatsapp-receiver-install", target=work, on_done=done)
        self._receiver_task.start()

    def _on_remove_receiver(self, _e: ft.ControlEvent) -> None:
        serial = self._device.serial
        self._remove_receiver_btn.disabled = True
        self._receiver_status.value = "Quitando auxiliar..."
        self._receiver_status.update()

        def work(_stop) -> None:
            uninstall_receiver(serial)

        def done(error: Optional[BaseException]) -> None:
            self._remove_receiver_btn.disabled = False
            if error:
                self._receiver_status.value = f"No se pudo quitar: {error}"
                self._receiver_status.color = theme.ERR
            else:
                self._receiver_status.value = "Auxiliar quitado del teléfono"
                self._receiver_status.color = theme.TEXT_MUTED
            self._receiver_status.update()
            self.view.update()

        self._receiver_task = BackgroundTask("whatsapp-receiver-remove", target=work, on_done=done)
        self._receiver_task.start()

    def _on_export(self, _e: ft.ControlEvent) -> None:
        try:
            n = int(self._count.value)
            if not 1 <= n <= 500:
                raise ValueError
        except (TypeError, ValueError):
            self._count.error_text = "Entero entre 1 y 500"
            self._count.update()
            return
        self._count.error_text = None

        include_media = bool(self._media.value)
        out_dir = (self._out_dir.value or "./exports").strip()
        serial = self._device.serial

        self._results = []
        self._log.clear()
        self._attach_log_handler()
        self._set_busy(True, "Conectando con el dispositivo...", indeterminate=True)
        print(f"[UI] Exportación INICIADA (n={n}, media={include_media}, serial={serial or 'auto'}, destino={out_dir})")

        def work(_stop) -> None:
            client = WhatsAppClient(DeviceManager(serial=serial), auto_watchers=True)
            try:
                self._results = client.export_top_chats(n=n, include_media=include_media, local_dir=out_dir)
            finally:
                client.close()

        self._task = BackgroundTask("whatsapp-export", target=work, on_done=self._on_done)
        self._task.start()

    def _on_done(self, error: Optional[BaseException]) -> None:
        self._detach_log_handler()
        ok = sum(1 for r in self._results if r.get("success"))
        total = len(self._results)
        if error:
            self._log.append(f"ERROR: {error}", theme.ERR)
            self._set_busy(False, f"Falló: {error}", progress=0)
        else:
            self._set_busy(False, f"Completado: {ok}/{total} chats exportados.", progress=1 if total else 0)
        print(f"[UI] Exportación FINALIZADA ({ok}/{total} OK)" + (f" error={error}" if error else ""))

    # ----------------------------------------------------------- progreso

    def _on_log(self, level: int, message: str) -> None:
        self._log.append(message, level_color(level))
        match = _PROGRESS_RE.search(message)
        if match:
            index, total = int(match.group(1)), int(match.group(2))
            self._progress.value = (index - 1) / total
            self._progress_text.value = f"Exportando chat {index} de {total}..."
            try:
                self._progress.update()
                self._progress_text.update()
            except Exception:  # noqa: BLE001
                pass

    def _set_busy(self, busy: bool, text: str, progress: Optional[float] = 0, indeterminate: bool = False) -> None:
        self._export_btn.disabled = busy
        self._count.disabled = busy
        self._media.disabled = busy
        self._out_dir.disabled = busy
        self._progress.value = None if indeterminate else progress
        self._progress_text.value = text
        try:
            self.view.update()
        except Exception:  # noqa: BLE001
            pass

    def _attach_log_handler(self) -> None:
        self._log_handler = UiLogHandler(self._on_log, LOG_PREFIXES)
        logging.getLogger().addHandler(self._log_handler)

    def _detach_log_handler(self) -> None:
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
