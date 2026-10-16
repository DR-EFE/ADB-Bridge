"""Pestaña «Comandos & Diagnóstico»: biblioteca de acciones directas ADB y diagnóstico rápido."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService

logger = logging.getLogger("ui.commands")


class CommandsPage:
    """Acciones rápidas de control, diagnóstico y captura de pantalla."""

    def __init__(self, device: DeviceService, on_log: Optional[Callable[[str, str], None]] = None) -> None:
        self._device = device
        self._on_log = on_log

        self._output_text = ft.Text(
            "Selecciona un comando para ejecutar en el dispositivo activo...",
            size=12,
            font_family=theme.MONO_FONT,
            selectable=True,
            color=theme.TEXT_MUTED,
        )

        self.view = ft.Column(
            expand=True,
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                # 1. Controles y Pantalla
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.VIDEOGAME_ASSET_OUTLINED, color=theme.ACCENT),
                                    ft.Text("Control de Pantalla y Teclas", weight=ft.FontWeight.BOLD, size=14),
                                ],
                                spacing=8,
                            ),
                            ft.Row(
                                wrap=True,
                                spacing=10,
                                controls=[
                                    ft.FilledButton("📸 Capturar Pantalla", on_click=self._take_screenshot),
                                    ft.OutlinedButton("☀️ Despertar Pantalla", on_click=lambda _: self._keyevent(224, "Despertar pantalla")),
                                    ft.OutlinedButton("🔒 Botón Power", on_click=lambda _: self._keyevent(26, "Power")),
                                    ft.OutlinedButton("🏠 Botón Inicio", on_click=lambda _: self._keyevent(3, "Home")),
                                    ft.OutlinedButton("◀ Botón Atrás", on_click=lambda _: self._keyevent(4, "Atrás")),
                                    ft.OutlinedButton("🔔 Bajar Notificaciones", on_click=self._expand_statusbar),
                                ],
                            ),
                        ],
                    )
                ),
                # 2. Diagnóstico del Sistema
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.ANALYTICS_OUTLINED, color=theme.OK),
                                    ft.Text("Diagnóstico & Rendimiento", weight=ft.FontWeight.BOLD, size=14),
                                ],
                                spacing=8,
                            ),
                            ft.Row(
                                wrap=True,
                                spacing=10,
                                controls=[
                                    ft.FilledButton("🔋 Diagnóstico Batería", on_click=self._diag_battery),
                                    ft.OutlinedButton("💾 Espacio en Disco", on_click=self._diag_storage),
                                    ft.OutlinedButton("📱 Info Completa", on_click=self._diag_system),
                                    ft.OutlinedButton("🌐 IP e Interfaces", on_click=self._diag_network),
                                    ft.OutlinedButton("🧭 Diagnóstico de conexión", on_click=self._diag_connection),
                                    ft.OutlinedButton("🔄 Reiniciar Teléfono", on_click=self._reboot_device),
                                ],
                            ),
                        ],
                    )
                ),
                # 3. Consola de Salida del Comando
                theme.card(
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.TERMINAL, size=16, color=theme.TEXT_MUTED),
                                    ft.Text("Resultado del Comando", weight=ft.FontWeight.W_600, size=13),
                                ],
                                spacing=6,
                            ),
                            ft.Container(
                                content=self._output_text,
                                padding=12,
                                bgcolor=theme.BG,
                                border_radius=8,
                                border=ft.Border.all(1, theme.BORDER),
                            ),
                        ],
                    )
                ),
            ],
        )

    def _get_device_or_alert(self):
        status = self._device.get_status()
        if not status.connected or not self._device.serial:
            msg = status.error or "No hay dispositivo conectado o listo para ejecutar comandos."
            self._set_output(f"[ERROR] {msg}")
            self._log(msg, theme.ERR)
            return None
        from ui_automation.adb_manager import get_adb_client
        return get_adb_client().device(self._device.serial)

    def _set_output(self, text: str) -> None:
        self._output_text.value = text
        self._output_text.color = theme.TEXT
        try:
            self._output_text.update()
        except Exception:
            pass

    def _log(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)

    def _keyevent(self, keycode: int, name: str) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        dev.keyevent(keycode)
        msg = f"Tecla enviada: {name} (keycode {keycode})"
        self._set_output(msg)
        self._log(msg, theme.OK)

    def _expand_statusbar(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        dev.shell("cmd statusbar expand-notifications")
        self._set_output("Panel de notificaciones desplegado.")
        self._log("Panel de notificaciones desplegado.", theme.OK)

    def _take_screenshot(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return

        def work():
            try:
                self._set_output("Capturando pantalla del teléfono...")
                pil_img = dev.screenshot()
                shots_dir = Path("logs/screenshots")
                shots_dir.mkdir(parents=True, exist_ok=True)
                filename = shots_dir / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
                pil_img.save(str(filename))
                msg = f"Captura guardada en: {filename.resolve()}"
                self._set_output(msg)
                self._log(msg, theme.OK)
                # Abrir imagen con el visor predeterminado de Windows
                os.startfile(str(filename.resolve()))
            except Exception as exc:
                err = f"Error al tomar captura de pantalla: {exc}"
                self._set_output(err)
                self._log(err, theme.ERR)

        threading.Thread(target=work, daemon=True).start()

    def _diag_battery(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        out = dev.shell("dumpsys battery")
        self._set_output(out)
        self._log("Diagnóstico de batería ejecutado.", theme.OK)

    def _diag_storage(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        out = dev.shell("df -h /sdcard")
        self._set_output(out)
        self._log("Espacio en almacenamiento consultado.", theme.OK)

    def _diag_system(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        props = [
            f"Modelo         : {dev.getprop('ro.product.model')}",
            f"Fabricante     : {dev.getprop('ro.product.manufacturer')}",
            f"Versión Android: {dev.getprop('ro.build.version.release')}",
            f"Nivel API (SDK): {dev.getprop('ro.build.version.sdk')}",
            f"Arquitectura   : {dev.getprop('ro.product.cpu.abi')}",
            f"Resolución     : {dev.shell('wm size').strip()}",
            f"Densidad (DPI) : {dev.shell('wm density').strip()}",
        ]
        text = "\n".join(props)
        self._set_output(text)
        self._log("Información de sistema consultada.", theme.OK)

    def _diag_network(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        out = dev.shell("ip -4 addr show")
        self._set_output(out)
        self._log("Interfaces de red consultadas.", theme.OK)

    def _diag_connection(self, _e: ft.ControlEvent) -> None:
        """Ejecuta diagnóstico completo de ADB, LAN, mDNS, endpoints y VPN."""
        self._set_output("Ejecutando diagnóstico de conectividad...")

        def work() -> None:
            try:
                report = self._device.run_network_diagnostics()
                payload = report.to_dict()
                lines = [
                    f"ADB: {payload['adb_path']}",
                    f"Version ADB: {payload['adb_version'].splitlines()[0] if payload['adb_version'] else 'desconocida'}",
                    f"Servidor ADB: {payload['adb_server_status'].splitlines()[0] if payload['adb_server_status'] else 'sin respuesta'}",
                    f"Estado ADB: {payload['adb_state']}",
                    f"Serial actual: {payload['current_serial'] or '—'}",
                    f"Interfaces: {', '.join(payload['active_networks']) or 'ninguna'}",
                    f"Gateways: {', '.join(payload['gateways']) or 'ninguno'}",
                    f"VPN: {', '.join(payload['vpn_interfaces']) or 'no detectada'}",
                    f"mDNS: {len(payload['mdns_services'])} servicio(s)",
                    "Endpoints conocidos: " + (", ".join(str(item.get("Endpoint")) for item in payload["known_endpoints"]) or "ninguno"),
                    "Servicios mDNS: " + (", ".join(str(item.get("endpoint")) for item in payload["mdns_services"]) or "ninguno"),
                    "Endpoints probados:",
                ]
                lines.extend(f"  {'OK' if item['reachable'] else 'FALLO'} {item['endpoint']}" + (f" ({item['error']})" if item.get('error') else "") for item in payload["tcp_tests"])
                lines.append("Recomendaciones:")
                lines.extend(f"  - {item}" for item in payload["recommendations"])
                self._set_output("\n".join(lines))
                self._log("Diagnóstico completo de conectividad ejecutado.", theme.OK)
            except Exception as exc:
                self._set_output(f"Error en diagnóstico de conectividad: {exc}")
                self._log("No se pudo completar el diagnóstico de conectividad.", theme.ERR)

        threading.Thread(target=work, name="network-diagnostics", daemon=True).start()

    def _reboot_device(self, _e: ft.ControlEvent) -> None:
        dev = self._get_device_or_alert()
        if not dev:
            return
        dev.reboot()
        msg = f"Dispositivo {dev.serial} reiniciando..."
        self._set_output(msg)
        self._log(msg, theme.WARN)

    def shutdown(self) -> None:
        pass
