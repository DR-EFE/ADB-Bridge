"""Tarjeta de estado del dispositivo con alertas de autorización y botón Pasar a Wi-Fi."""

from __future__ import annotations

import logging
from typing import Callable, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceStatus

logger = logging.getLogger("ui.status_card")


class StatusCard:
    """Muestra un :class:`DeviceStatus` con feedback visual enriquecido."""

    def __init__(self, on_convert_wifi: Optional[Callable[[], None]] = None) -> None:
        self._on_convert_wifi = on_convert_wifi

        self._dot = ft.Container(width=14, height=14, border_radius=7, bgcolor=theme.ERR)
        self._headline = ft.Text("Desconectado", weight=ft.FontWeight.BOLD, size=15)
        self._model = self._row_value()
        self._endpoint = self._row_value()
        self._battery = self._row_value()
        self._android_ver = self._row_value()
        self._adb_state = self._row_value()

        # Banner de alerta para dispositivos no autorizados
        self._unauthorized_alert = ft.Container(
            visible=False,
            bgcolor="#332400",
            border=ft.Border.all(1, theme.WARN),
            border_radius=8,
            padding=10,
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=theme.WARN, size=20),
                            ft.Text("Autorización requerida", weight=ft.FontWeight.BOLD, color=theme.WARN, size=13),
                        ],
                        spacing=6,
                    ),
                    ft.Text(
                        "Desbloquea la pantalla de tu celular y pulsa 'Permitir siempre la depuración USB'.",
                        size=11,
                        color=theme.TEXT,
                    ),
                ],
            ),
        )

        # Badge/Indicador informativo de modo de conexión (USB vs Wi-Fi)
        self._mode_icon = ft.Icon(ft.Icons.WIFI, size=16, color=theme.OK)
        self._mode_text = ft.Text("", size=11, weight=ft.FontWeight.W_500, expand=True)
        self._mode_badge = ft.Container(
            visible=False,
            border_radius=6,
            padding=ft.Padding(10, 6, 10, 6),
            content=ft.Row(
                spacing=8,
                controls=[
                    self._mode_icon,
                    self._mode_text,
                ],
            ),
        )

        # Botón secundario para forzar conversión USB a Wi-Fi manualmente si hiciera falta
        self._btn_wifi_convert = ft.OutlinedButton(
            "Forzar paso a Wi-Fi",
            icon=ft.Icons.WIFI,
            visible=False,
            on_click=self._handle_wifi_convert,
        )

        self.view = theme.card(
            ft.Column(
                spacing=10,
                controls=[
                    ft.Row([self._dot, self._headline], spacing=10),
                    self._unauthorized_alert,
                    self._mode_badge,
                    ft.Divider(height=1, color=theme.BORDER),
                    self._labeled(ft.Icons.PHONE_ANDROID, "Modelo", self._model),
                    self._labeled(ft.Icons.WIFI, "Serial/IP", self._endpoint),
                    self._labeled(ft.Icons.BATTERY_CHARGING_FULL, "Batería", self._battery),
                    self._labeled(ft.Icons.INFO_OUTLINE, "Android", self._android_ver),
                    self._labeled(ft.Icons.TERMINAL, "Estado", self._adb_state),
                    self._btn_wifi_convert,
                ],
            )
        )

    @staticmethod
    def _row_value() -> ft.Text:
        return ft.Text("—", size=13, color=theme.TEXT, selectable=True)

    @staticmethod
    def _labeled(icon: ft.IconData, label: str, value: ft.Text) -> ft.Control:
        return ft.Row(
            spacing=8,
            controls=[
                ft.Icon(icon, size=16, color=theme.TEXT_MUTED),
                ft.Text(f"{label}:", size=12, color=theme.TEXT_MUTED, width=72),
                value,
            ],
        )

    def _handle_wifi_convert(self, _e: ft.ControlEvent) -> None:
        if self._on_convert_wifi:
            self._on_convert_wifi()

    def set_message(self, text: str, color: str = theme.WARN) -> None:
        """Muestra un mensaje transitorio (p. ej. 'Iniciando ADB...')."""
        self._dot.bgcolor = color
        self._headline.value = text
        self._safe_update()

    def update_status(self, status: DeviceStatus) -> None:
        """Refresca todos los campos a partir del estado detectado."""
        if status.adb_state == "unauthorized":
            self._dot.bgcolor = theme.WARN
            self._headline.value = "No Autorizado"
            self._unauthorized_alert.visible = True
            self._mode_badge.visible = False
            self._btn_wifi_convert.visible = False
        elif status.connected:
            self._dot.bgcolor = theme.OK
            self._headline.value = "Conectado"
            self._unauthorized_alert.visible = False
            is_usb = bool(status.serial and ":" not in status.serial)
            self._mode_badge.visible = True
            if is_usb:
                self._mode_badge.bgcolor = "#332400"
                self._mode_icon.name = ft.Icons.USB
                self._mode_icon.color = theme.WARN
                self._mode_text.value = "USB activo: Conmutando a Wi-Fi automáticamente..."
                self._mode_text.color = theme.WARN
                self._btn_wifi_convert.visible = True
            else:
                self._mode_badge.bgcolor = "#0d2b18"
                self._mode_icon.name = ft.Icons.WIFI
                self._mode_icon.color = theme.OK
                self._mode_text.value = "Inalámbrico (Wi-Fi) ✓ Cable no requerido"
                self._mode_text.color = theme.OK
                self._btn_wifi_convert.visible = False
        else:
            self._dot.bgcolor = theme.ERR
            self._headline.value = "Desconectado"
            self._unauthorized_alert.visible = False
            self._mode_badge.visible = False
            self._btn_wifi_convert.visible = False

        self._model.value = status.model
        self._endpoint.value = status.serial or "—"

        bat_text = "—"
        if status.battery is not None:
            bat_text = f"{status.battery}%"
            if status.temperature is not None:
                bat_text += f" ({status.temperature}°C)"
        self._battery.value = bat_text

        self._android_ver.value = status.android_version or "—"
        self._adb_state.value = status.adb_state.upper() if status.adb_state else "—"

        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.view.update()
        except Exception:
            pass
