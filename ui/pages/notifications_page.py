"""Pestaña «Monitor de Notificaciones»: encender/apagar, intervalo, IA e historial en vivo."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import flet as ft

from .. import theme
from ..components.log_view import LogView, UiLogHandler, level_color
from ..services.device_service import DeviceService
from ..services.monitor_service import UiNotificationMonitor
from ..tasks import BackgroundTask

logger = logging.getLogger("ui.notifications")

MAX_HISTORY = 100
LOG_PREFIXES = ("ui_automation.monitor", "ui_automation.notifications", "ui_automation.ollama", "ui.notifications")


class NotificationsPage:
    """Contenido de la pestaña. El control a insertar es ``.view``."""

    def __init__(self, device: DeviceService) -> None:
        self._device = device
        self._monitor: Optional[UiNotificationMonitor] = None
        self._task: Optional[BackgroundTask] = None
        self._log_handler: Optional[UiLogHandler] = None
        self._new_count = 0
        self._important_count = 0

        self._toggle_btn = ft.FilledButton(
            "Encender", icon=ft.Icons.PLAY_ARROW, bgcolor=theme.OK, color=theme.BG, on_click=self._on_toggle
        )
        self._interval_label = ft.Text("Intervalo: 3 s", size=13)
        self._interval = ft.Slider(min=1, max=30, divisions=29, value=3, label="{value} s", width=260,
                                   on_change=self._on_interval_change)
        self._ai_switch = ft.Switch(label="IA local (Ollama)", value=True)
        self._state_chip = ft.Text("Detenido", size=12, color=theme.TEXT_MUTED)
        self._stats = ft.Text("Nuevas: 0  |  Importantes: 0", size=12, color=theme.TEXT_MUTED)

        self._history = ft.ListView(expand=True, spacing=8, auto_scroll=False)
        self._empty_hint = ft.Text("Aún no hay notificaciones importantes.", color=theme.TEXT_MUTED, size=13)
        self._history.controls.append(self._empty_hint)
        self._log = LogView(height=130)

        self.view = ft.Column(
            expand=True,
            spacing=14,
            controls=[
                theme.card(
                    ft.Row(
                        wrap=True,
                        spacing=24,
                        run_spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._toggle_btn,
                            ft.Column([self._interval_label, self._interval], spacing=0),
                            self._ai_switch,
                            ft.Column([self._state_chip, self._stats], spacing=2),
                        ],
                    )
                ),
                ft.Text("Historial en vivo (importantes)", size=14, weight=ft.FontWeight.W_600),
                theme.card(self._history, expand=True),
                self._log.view,
            ],
        )

    # ---------------------------------------------------------- controles

    def _on_interval_change(self, _e: ft.ControlEvent) -> None:
        self._interval_label.value = f"Intervalo: {int(self._interval.value)} s"
        self._interval_label.update()

    def _on_toggle(self, _e: ft.ControlEvent) -> None:
        if self._task and self._task.is_running:
            self._stop_monitor()
        else:
            self._start_monitor()

    def shutdown(self) -> None:
        """Detiene el monitor si sigue activo (al cerrar la app)."""
        self._stop_monitor()

    # ------------------------------------------------------- ciclo de vida

    def _start_monitor(self) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            self._log.append("Hay varios dispositivos conectados. Selecciona uno obligatoriamente en el panel lateral antes de encender el monitor.", theme.ERR)
            return

        serial = self._device.serial
        if not serial or not status.connected:
            self._log.append(f"No hay dispositivo conectado o listo: {status.error or 'Selecciona uno en el panel lateral'}", theme.ERR)
            return

        interval = float(self._interval.value)
        use_ai = bool(self._ai_switch.value)

        self._monitor = UiNotificationMonitor(
            serial=serial,
            poll_interval=interval,
            use_ai=use_ai,
            on_notification_callback=self._on_any_notification,
            on_important=self._on_important,
        )
        self._task = BackgroundTask(
            "monitor-notificaciones",
            target=lambda _stop: self._monitor.start(),  # bloquea hasta stop(); vive en su hilo
            on_done=self._on_task_done,
            on_stop=self._monitor.stop,
        )

        # Registrar supervisor con el servicio de dispositivos
        self._device.register_supervisor(
            serial,
            on_stop=self._stop_monitor,
            on_restart=self._start_monitor,
        )

        self._attach_log_handler()
        self._new_count = self._important_count = 0
        self._update_stats()
        self._set_running(True)
        print(f"[UI] Monitor ENCENDIDO (serial={serial}, intervalo={interval:.0f}s, IA={use_ai})")
        self._task.start()

    def _stop_monitor(self) -> None:
        if self._task and self._task.is_running:
            self._state_chip.value = "Deteniendo..."
            self._toggle_btn.disabled = True
            self._safe_update()
            print("[UI] Monitor APAGANDO...")
            self._task.stop()

    def _on_task_done(self, error: Optional[BaseException]) -> None:
        self._detach_log_handler()
        print("[UI] Monitor APAGADO" + (f" (error: {error})" if error else ""))
        self._set_running(False)
        if error:
            self._log.append(f"ERROR: {error}", theme.ERR)

    # ----------------------------------------------------------- eventos

    def _on_any_notification(self, _item: Any) -> None:
        self._new_count += 1
        self._update_stats()

    def _on_important(self, event: Dict[str, Any]) -> None:
        self._important_count += 1
        if self._empty_hint in self._history.controls:
            self._history.controls.remove(self._empty_hint)
        self._history.controls.insert(0, self._build_event_card(event))
        del self._history.controls[MAX_HISTORY:]
        self._update_stats()
        try:
            self._history.update()
        except Exception:  # noqa: BLE001
            pass

    def _build_event_card(self, event: Dict[str, Any]) -> ft.Control:
        color = theme.TYPE_COLORS.get(event["tipo"], theme.ACCENT)
        code = event.get("codigo")
        header = [
            ft.Container(
                ft.Text(event["tipo"], size=11, weight=ft.FontWeight.BOLD, color=theme.BG),
                bgcolor=color, padding=ft.Padding.symmetric(horizontal=8, vertical=2), border_radius=6,
            ),
            ft.Text(event.get("titulo") or "(sin título)", weight=ft.FontWeight.W_600, expand=True,
                    overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
            ft.Text(datetime.now().strftime("%H:%M:%S"), size=11, color=theme.TEXT_MUTED),
        ]
        body = [ft.Text(event.get("texto") or "", size=12, color=theme.TEXT, max_lines=3,
                        overflow=ft.TextOverflow.ELLIPSIS)]
        if code:
            body.append(ft.Text(f"CÓDIGO: {code}", size=18, weight=ft.FontWeight.BOLD, color=color, selectable=True))
        body.append(ft.Text(f"{event.get('package', '')}  ·  fuente: {event.get('fuente', '?')}",
                            size=11, color=theme.TEXT_MUTED))
        return ft.Container(
            ft.Column([ft.Row(header, spacing=8)] + body, spacing=4),
            padding=12, bgcolor=theme.SURFACE_ALT, border_radius=10,
            border=ft.Border.all(1, theme.BORDER),
        )

    # -------------------------------------------------------------- UI

    def _set_running(self, running: bool) -> None:
        self._toggle_btn.disabled = False
        self._toggle_btn.content = "Apagar" if running else "Encender"
        self._toggle_btn.icon = ft.Icons.STOP if running else ft.Icons.PLAY_ARROW
        self._toggle_btn.bgcolor = theme.ERR if running else theme.OK
        self._state_chip.value = "Monitoreando..." if running else "Detenido"
        self._state_chip.color = theme.OK if running else theme.TEXT_MUTED
        self._interval.disabled = running
        self._ai_switch.disabled = running
        self._safe_update()

    def _update_stats(self) -> None:
        self._stats.value = f"Nuevas: {self._new_count}  |  Importantes: {self._important_count}"
        try:
            self._stats.update()
        except Exception:  # noqa: BLE001
            pass

    def _safe_update(self) -> None:
        try:
            self.view.update()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ logging

    def _attach_log_handler(self) -> None:
        self._log.clear()
        self._log_handler = UiLogHandler(lambda lvl, msg: self._log.append(msg, level_color(lvl)), LOG_PREFIXES)
        logging.getLogger().addHandler(self._log_handler)

    def _detach_log_handler(self) -> None:
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
