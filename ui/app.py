"""Clase principal de la aplicación: ensambla sidebar, pestañas y logging global."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import flet as ft

from ui_automation.adb_manager import stop_reconnect_watchdog

from . import theme
from .components.job_drawer import JobDrawer
from .components.sidebar import Sidebar
from .pages.backup_page import BackupPage
from .pages.commands_page import CommandsPage
from .pages.devices_page import DevicesPage
from .pages.explorer_page import ExplorerPage
from .pages.logs_page import LogsPage
from .pages.notifications_page import NotificationsPage
from .pages.whatsapp_page import WhatsAppPage
from .services.device_service import DeviceService
from .services.job_queue_service import JobQueueService

logger = logging.getLogger("ui.app")

# Las notificaciones tienen su propia pestaña y persistencia. Estos namespaces
# no deben contaminar el registro técnico global (ADB, red, transferencias,
# backups, Nmap y errores de la aplicación).
NOTIFICATION_LOGGER_PREFIXES = (
    "ui.notifications",
    "ui_automation.notifications",
    "ui_automation.monitor",
)


class GlobalUiLogHandler(logging.Handler):
    """Handler de logging que alimenta la pestaña de Registro en tiempo real."""

    def __init__(self, logs_page: LogsPage) -> None:
        super().__init__(level=logging.INFO)
        self.logs_page = logs_page
        self.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(NOTIFICATION_LOGGER_PREFIXES):
            return
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
        self.job_queue = JobQueueService()

        theme.apply_theme(page, title="Android ADB Tools - Control Center")

        # Pestañas principales
        self.devices = DevicesPage(
            self.device,
            page=page,
            on_log=self._on_custom_log,
            on_device_changed=lambda _: self.sidebar.refresh_now(),
        )
        self.logs = LogsPage(page)
        self.notifications = NotificationsPage(self.device)
        self.whatsapp = WhatsAppPage(self.device, job_queue=self.job_queue)
        self.explorer = ExplorerPage(self.device, page=page, on_log=self._on_custom_log)
        self.backup = BackupPage(self.device, page=page, on_log=self._on_custom_log, job_queue=self.job_queue)
        self.commands = CommandsPage(self.device, on_log=self._on_custom_log)

        # Conectar logging global de Python al visor de logs
        self._log_handler = GlobalUiLogHandler(self.logs)
        logging.getLogger().addHandler(self._log_handler)

        self._sidebar_visible = True
        self._sidebar_divider = ft.VerticalDivider(width=1, color=theme.BORDER)

        self._sidebar_toggle_btn = ft.IconButton(
            icon=ft.Icons.MENU_OPEN,
            tooltip="Ocultar panel lateral (Dispositivos)",
            on_click=self._toggle_sidebar,
        )

        self._active_device_chip = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            border_radius=8,
            bgcolor=theme.ACCENT_LIGHT if self.device.serial else theme.SURFACE_ALT,
            border=ft.Border.all(1, theme.ACCENT if self.device.serial else theme.BORDER),
            content=ft.Row(
                spacing=6,
                controls=[
                    ft.Icon(ft.Icons.PHONE_ANDROID, size=15, color=theme.OK if self.device.serial else theme.TEXT_MUTED),
                    ft.Text(self.device.serial or "Sin dispositivo", size=11, weight=ft.FontWeight.W_600, color=theme.TEXT),
                ],
            ),
            tooltip="Dispositivo activo. Clic para mostrar/ocultar panel de dispositivos",
            on_click=self._toggle_sidebar,
        )

        self._theme_toggle_btn = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            tooltip="Cambiar entre modo claro y oscuro",
            on_click=self._toggle_theme,
        )

        self.sidebar = Sidebar(
            self.device,
            on_status=self._on_device_status_update,
            on_log=self._on_custom_log,
            on_collapse=self._toggle_sidebar,
        )

        # Drawer inferior transversal para la cola de trabajos
        self.job_drawer = JobDrawer(
            self.job_queue,
            on_badge_update=self._update_window_title_badge,
        )

        # Sistema de pestañas con barra superior integrada y botón de menú desplegable
        self._tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="Dispositivos", icon=ft.Icons.PHONELINK_SETUP_OUTLINED),
                ft.Tab(label="Explorador & Transferencias", icon=ft.Icons.FOLDER_SPECIAL_OUTLINED),
                ft.Tab(label="Backup avanzado", icon=ft.Icons.BACKUP_OUTLINED),
                ft.Tab(label="WhatsApp Extractor", icon=ft.Icons.CHAT_OUTLINED),
                ft.Tab(label="Monitor de Notificaciones", icon=ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED),
                ft.Tab(label="Comandos & Diagnóstico", icon=ft.Icons.BOLT_OUTLINED),
                ft.Tab(label="Registro & Salida (Log)", icon=ft.Icons.RECEIPT_LONG_OUTLINED),
            ]
        )

        self._top_bar = ft.Container(
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            bgcolor=theme.SURFACE,
            content=ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self._sidebar_toggle_btn,
                    ft.Container(expand=True, content=self._tab_bar),
                    self._active_device_chip,
                    self._theme_toggle_btn,
                ],
            ),
        )

        self.tabs = ft.Tabs(
            length=7,
            selected_index=0,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    self._top_bar,
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            theme.module_view(
                                "01",
                                "Dispositivos",
                                "Conexiones USB/Wi-Fi | seleccion segura | estado del equipo",
                                self.devices.view,
                            ),
                            theme.module_view(
                                "02",
                                "Explorador y transferencias",
                                "Archivos del dispositivo | navegacion | cola de transferencias",
                                self.explorer.view,
                            ),
                            theme.module_view(
                                "03",
                                "Respaldo avanzado",
                                "Categorias de respaldo | procesamiento | exportacion",
                                self.backup.view,
                            ),
                            theme.module_view(
                                "04",
                                "WhatsApp extractor",
                                "Exploracion de chats | exportacion | procesamiento",
                                self.whatsapp.view,
                            ),
                            theme.module_view(
                                "05",
                                "Monitor de notificaciones",
                                "Captura en vivo | alertas | filtros e historial",
                                self.notifications.view,
                            ),
                            theme.module_view(
                                "06",
                                "Comandos y diagnostico",
                                "Herramientas ADB | diagnostico | captura",
                                self.commands.view,
                            ),
                            theme.module_view(
                                "07",
                                "Registro y salida",
                                "Actividad tecnica en tiempo real | filtros | exportacion",
                                self.logs.view,
                            ),
                        ],
                    ),
                ],
            ),
        )

        # Layout general: Sidebar a la izquierda (desplegable) + Columna con Tabs y Drawer
        page.add(
            ft.Row(
                expand=True,
                spacing=0,
                controls=[
                    self.sidebar.view,
                    self._sidebar_divider,
                    ft.Column(
                        expand=True,
                        spacing=0,
                        controls=[
                            self.tabs,
                            self.job_drawer.view,
                        ],
                    ),
                ],
            )
        )

    def _toggle_sidebar(self, _e=None) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self.sidebar.view.visible = self._sidebar_visible
        self._sidebar_divider.visible = self._sidebar_visible
        self._sidebar_toggle_btn.icon = ft.Icons.MENU if not self._sidebar_visible else ft.Icons.MENU_OPEN
        self._sidebar_toggle_btn.tooltip = (
            "Mostrar panel de dispositivos" if not self._sidebar_visible else "Ocultar panel lateral (Dispositivos)"
        )
        try:
            self.page.update()
        except Exception:
            pass

    def _toggle_theme(self, _e=None) -> None:
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            self.page.theme_mode = ft.ThemeMode.DARK
            self._theme_toggle_btn.icon = ft.Icons.LIGHT_MODE_OUTLINED
        else:
            self.page.theme_mode = ft.ThemeMode.LIGHT
            self._theme_toggle_btn.icon = ft.Icons.DARK_MODE_OUTLINED
        try:
            self.page.update()
        except Exception:
            pass

    def _on_device_status_update(self, status: DeviceStatus) -> None:
        if hasattr(self, "_active_device_chip"):
            color = theme.OK if status.connected else theme.TEXT_MUTED
            text = status.serial or "Sin dispositivo"
            self._active_device_chip.content.controls[0].color = color
            self._active_device_chip.content.controls[1].value = text
            self._active_device_chip.content.controls[1].color = theme.TEXT if status.connected else theme.TEXT_MUTED
            self._active_device_chip.bgcolor = theme.ACCENT_LIGHT if status.connected else theme.SURFACE_ALT
            self._active_device_chip.border = ft.Border.all(1, theme.ACCENT if status.connected else theme.BORDER)
            try:
                self._active_device_chip.update()
            except Exception:
                pass

    def _update_window_title_badge(self, active_count: int) -> None:
        """Actualiza el título de la ventana con un badge del conteo de trabajos activos."""
        base_title = "Android ADB Tools - Control Center"
        if active_count > 0:
            self.page.title = f"({active_count}) {base_title}"
        else:
            self.page.title = base_title
        try:
            self.page.update()
        except Exception:
            pass

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
        polling_started = False
        try:
            self.device.init_adb()
            self.device.start_usb_auto_connect_monitor()
            # El sondeo USB debe iniciar inmediatamente; la reconexión de
            # endpoints Wi-Fi históricos se ejecuta en paralelo y no bloquea UI.
            self.sidebar.start_polling()
            polling_started = True

            def reconnect_saved() -> None:
                reconnect_results = self.device.auto_reconnect_saved_devices()
                connected = sum(1 for result in reconnect_results if result.success)
                if reconnect_results:
                    self.sidebar.set_message(
                        f"Reconexión automática: {connected}/{len(reconnect_results)} dispositivo(s).",
                        theme.OK if connected else theme.WARN,
                    )

            threading.Thread(target=reconnect_saved, name="auto-reconnect-saved", daemon=True).start()
        except Exception as exc:
            logger.error("No se pudo iniciar el daemon ADB: %s", exc)
            self.sidebar.set_message("ADB no disponible", theme.ERR)
        # El polling ya se inicia antes de la reconexión automática cuando ADB
        # arranca correctamente. Este fallback cubre errores parciales de inicio.
        if not polling_started:
            self.sidebar.start_polling()

    def shutdown(self) -> None:
        """Cierra la aplicación y libera recursos."""
        logger.info("Cerrando Android Tools...")
        self.sidebar.stop_polling()
        self.device.stop_usb_auto_connect_monitor()
        for page in (self.devices, self.explorer, self.backup, self.whatsapp, self.notifications, self.commands, self.logs):
            page.shutdown()
        self.job_queue.shutdown()
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
        stop_reconnect_watchdog()
