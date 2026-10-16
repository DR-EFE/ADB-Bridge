"""Clase principal de la aplicación: ensambla sidebar, pestañas y logging global."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import flet as ft

from ui_automation.adb_manager import stop_reconnect_watchdog

from . import theme
from .components.sidebar import Sidebar
from .pages.commands_page import CommandsPage
from .pages.devices_page import DevicesPage
from .pages.explorer_page import ExplorerPage
from .pages.logs_page import LogsPage
from .pages.notifications_page import NotificationsPage
from .pages.whatsapp_page import WhatsAppPage
from .services.device_service import DeviceService

logger = logging.getLogger("ui.app")


class GlobalUiLogHandler(logging.Handler):
    """Handler de logging que alimenta la pestaña de Registro en tiempo real."""

    def __init__(self, logs_page: LogsPage) -> None:
        super().__init__(level=logging.INFO)
        self.logs_page = logs_page
        self.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.logs_page.append_log(record.levelno, msg)
        except Exception:
            pass


class AndroidToolsApp:
    """Ventana principal moderna de Android Tools (Flet)."""

    def __init__(self, page: ft.Page, serial: Optional[str] = None) -> None:
        self.page = page
        self.device = DeviceService(serial=serial)

        theme.apply_theme(page, title="Android ADB Tools - Control Center")

        # Pestañas principales
        self.devices = DevicesPage(
            self.device,
            on_log=self._on_custom_log,
            on_device_changed=lambda _: self.sidebar.refresh_now(),
        )
        self.logs = LogsPage(page)
        self.notifications = NotificationsPage(self.device)
        self.whatsapp = WhatsAppPage(self.device)
        self.explorer = ExplorerPage(self.device, on_log=self._on_custom_log)
        self.commands = CommandsPage(self.device, on_log=self._on_custom_log)

        # Conectar logging global de Python al visor de logs
        self._log_handler = GlobalUiLogHandler(self.logs)
        logging.getLogger().addHandler(self._log_handler)

        self.sidebar = Sidebar(self.device, on_log=self._on_custom_log)

        # Sistema de pestañas con iconos modernos
        tabs = ft.Tabs(
            length=6,
            selected_index=0,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Dispositivos", icon=ft.Icons.PHONELINK_SETUP_OUTLINED),
                            ft.Tab(label="Explorador & Transferencias", icon=ft.Icons.FOLDER_SPECIAL_OUTLINED),
                            ft.Tab(label="WhatsApp Extractor", icon=ft.Icons.CHAT_OUTLINED),
                            ft.Tab(label="Monitor de Notificaciones", icon=ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED),
                            ft.Tab(label="Comandos & Diagnóstico", icon=ft.Icons.BOLT_OUTLINED),
                            ft.Tab(label="Registro & Salida (Log)", icon=ft.Icons.RECEIPT_LONG_OUTLINED),
                        ]
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            ft.Container(self.devices.view, padding=16),
                            ft.Container(self.explorer.view, padding=16),
                            ft.Container(self.whatsapp.view, padding=16),
                            ft.Container(self.notifications.view, padding=16),
                            ft.Container(self.commands.view, padding=16),
                            ft.Container(self.logs.view, padding=16),
                        ],
                    ),
                ],
            ),
        )

        page.add(
            ft.Row(
                expand=True,
                spacing=0,
                controls=[
                    self.sidebar.view,
                    ft.VerticalDivider(width=1, color=theme.BORDER),
                    tabs,
                ],
            )
        )

    def _on_custom_log(self, text: str, color: str = theme.TEXT) -> None:
        """Permite que cualquier componente agregue mensajes directamente al registro."""
        lvl = logging.WARNING if color == theme.WARN else (logging.ERROR if color == theme.ERR else logging.INFO)
        now = logging.Formatter().formatTime(logging.makeLogRecord({}))
        self.logs.append_log(lvl, f"[{now}] {text}")

    def start(self) -> None:
        """Inicializa ADB en segundo plano y arranca el sondeo del sidebar."""
        threading.Thread(target=self._boot, name="app-boot", daemon=True).start()

    def _boot(self) -> None:
        self.sidebar.set_message("Iniciando ADB...", theme.WARN)
        try:
            self.device.init_adb()
        except Exception as exc:
            logger.error("No se pudo iniciar el daemon ADB: %s", exc)
            self.sidebar.set_message("ADB no disponible", theme.ERR)
        self.sidebar.start_polling()

    def shutdown(self) -> None:
        """Cierra la aplicación y libera recursos."""
        logger.info("Cerrando Android Tools...")
        self.sidebar.stop_polling()
        for page in (self.devices, self.explorer, self.whatsapp, self.notifications, self.commands, self.logs):
            page.shutdown()
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
        stop_reconnect_watchdog()
