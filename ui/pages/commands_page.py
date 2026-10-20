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

        # Tiles visuales de diagnóstico técnico
        self._tile_adb_status = ft.Text("Versión: — | Servidor: —", size=12, color=theme.TEXT_MUTED)
        self._tile_network = ft.Text("PC: — | Móvil: —", size=12, color=theme.TEXT_MUTED)
        self._tile_device_info = ft.Text("Dispositivo: — | Android: —", size=12, color=theme.TEXT_MUTED)
        self._tile_hardware = ft.Text("Batería: — | Almacenamiento: —", size=12, color=theme.TEXT_MUTED)
        self._diag_progress = ft.ProgressBar(visible=False, color=theme.ACCENT, bgcolor=theme.SURFACE_ALT)

        self.view = ft.Column(
            expand=True,
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                # 1. Panel de Diagnóstico Técnico Integral (Requerimiento #10)
                theme.card(
                    ft.Column(
                        spacing=12,
                        controls=[
                            ft.Row(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(ft.Icons.HEALTH_AND_SAFETY_OUTLINED, color=theme.ACCENT, size=20),
                                            ft.Text("Diagnóstico Técnico Integral", weight=ft.FontWeight.BOLD, size=15),
                                        ],
                                        spacing=8,
                                    ),
                                    ft.FilledButton(
                                        "Ejecutar Diagnóstico Completo",
                                        icon=ft.Icons.HEALTH_AND_SAFETY,
                                        on_click=self._run_full_technical_diagnostics,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            self._diag_progress,
                            ft.ResponsiveRow(
                                columns=12,
                                spacing=10,
                                run_spacing=10,
                                controls=[
                                    ft.Container(
                                        col={"sm": 12, "md": 6, "lg": 3},
                                        bgcolor=theme.SURFACE_ALT,
                                        border_radius=8,
                                        padding=12,
                                        border=ft.Border.all(1, theme.BORDER),
                                        content=ft.Column(
                                            spacing=4,
                                            controls=[
                                                ft.Row([ft.Icon(ft.Icons.TERMINAL, size=16, color=theme.ACCENT), ft.Text("Entorno ADB", size=12, weight=ft.FontWeight.BOLD)]),
                                                self._tile_adb_status,
                                            ],
                                        ),
                                    ),
                                    ft.Container(
                                        col={"sm": 12, "md": 6, "lg": 3},
                                        bgcolor=theme.SURFACE_ALT,
                                        border_radius=8,
                                        padding=12,
                                        border=ft.Border.all(1, theme.BORDER),
                                        content=ft.Column(
                                            spacing=4,
                                            controls=[
                                                ft.Row([ft.Icon(ft.Icons.WIFI, size=16, color=theme.OK), ft.Text("Red y Puertos", size=12, weight=ft.FontWeight.BOLD)]),
                                                self._tile_network,
                                            ],
                                        ),
                                    ),
                                    ft.Container(
                                        col={"sm": 12, "md": 6, "lg": 3},
                                        bgcolor=theme.SURFACE_ALT,
                                        border_radius=8,
                                        padding=12,
                                        border=ft.Border.all(1, theme.BORDER),
                                        content=ft.Column(
                                            spacing=4,
                                            controls=[
                                                ft.Row([ft.Icon(ft.Icons.PHONE_ANDROID, size=16, color=theme.WARN), ft.Text("Dispositivo & SO", size=12, weight=ft.FontWeight.BOLD)]),
                                                self._tile_device_info,
                                            ],
                                        ),
                                    ),
                                    ft.Container(
                                        col={"sm": 12, "md": 6, "lg": 3},
                                        bgcolor=theme.SURFACE_ALT,
                                        border_radius=8,
                                        padding=12,
                                        border=ft.Border.all(1, theme.BORDER),
                                        content=ft.Column(
                                            spacing=4,
                                            controls=[
                                                ft.Row([ft.Icon(ft.Icons.BATTERY_CHARGING_FULL, size=16, color=theme.OK), ft.Text("Batería y Almacenamiento", size=12, weight=ft.FontWeight.BOLD)]),
                                                self._tile_hardware,
                                            ],
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    )
                ),
                # 2. Controles y Pantalla
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
                                    ft.FilledButton("Capturar Pantalla", icon=ft.Icons.CAMERA_ALT, on_click=self._take_screenshot),
                                    ft.OutlinedButton("Despertar Pantalla", icon=ft.Icons.WB_SUNNY, on_click=lambda _: self._keyevent(224, "Despertar pantalla")),
                                    ft.OutlinedButton("Botón Power", icon=ft.Icons.POWER_SETTINGS_NEW, on_click=lambda _: self._keyevent(26, "Power")),
                                    ft.OutlinedButton("Botón Inicio", icon=ft.Icons.HOME, on_click=lambda _: self._keyevent(3, "Home")),
                                    ft.OutlinedButton("Botón Atrás", icon=ft.Icons.ARROW_BACK, on_click=lambda _: self._keyevent(4, "Atrás")),
                                    ft.OutlinedButton("Bajar Notificaciones", icon=ft.Icons.NOTIFICATIONS, on_click=self._expand_statusbar),
                                ],
                            ),
                        ],
                    )
                ),
                # 3. Acciones Individuales de Diagnóstico
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.ANALYTICS_OUTLINED, color=theme.OK),
                                    ft.Text("Consultas Específicas & Acciones", weight=ft.FontWeight.BOLD, size=14),
                                ],
                                spacing=8,
                            ),
                            ft.Row(
                                wrap=True,
                                spacing=10,
                                controls=[
                                    ft.FilledButton("Diagnóstico Batería", icon=ft.Icons.BATTERY_CHARGING_FULL, on_click=self._diag_battery),
                                    ft.OutlinedButton("Espacio en Disco", icon=ft.Icons.STORAGE, on_click=self._diag_storage),
                                    ft.OutlinedButton("Info Completa", icon=ft.Icons.PHONE_ANDROID, on_click=self._diag_system),
                                    ft.OutlinedButton("IP e Interfaces", icon=ft.Icons.LANGUAGE, on_click=self._diag_network),
                                    ft.OutlinedButton("Diagnóstico de Conexión", icon=ft.Icons.EXPLORE, on_click=self._diag_connection),
                                    ft.OutlinedButton("Reiniciar Teléfono", icon=ft.Icons.RESTART_ALT, on_click=self._reboot_device),
                                ],
                            ),
                        ],
                    )
                ),
                # 4. Consola de Salida del Comando
                theme.card(
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.TERMINAL, size=16, color=theme.TEXT_MUTED),
                                    ft.Text("Detalle & Consola de Diagnóstico", weight=ft.FontWeight.W_600, size=13),
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
        if status.requires_selection:
            msg = "Hay varios dispositivos conectados. Selecciona uno obligatoriamente en el panel lateral antes de ejecutar comandos."
            self._set_output(f"[ERROR] {msg}")
            self._log(msg, theme.ERR)
            return None
        if not status.connected or not self._device.serial:
            msg = status.error or "No hay dispositivo conectado o listo para ejecutar comandos."
            self._set_output(f"[ERROR] {msg}")
            self._log(msg, theme.ERR)
            return None
        from ui_automation.adb_manager import get_adb_client
        logger.info("Comandos ADB ejecutándose en dispositivo seleccionado: %s", self._device.serial)
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

    def _run_full_technical_diagnostics(self, _e: Optional[ft.ControlEvent] = None) -> None:
        """Ejecuta el diagnóstico técnico integral y actualiza tanto los tiles como el reporte detallado."""
        self._set_output("Iniciando diagnóstico técnico completo de ADB, red, conectividad y hardware...")
        self._diag_progress.visible = True
        try:
            self._diag_progress.update()
        except Exception:
            pass

        def work() -> None:
            try:
                report = self._device.run_technical_diagnostics()
                data = report.to_dict()

                # Actualizar tiles visuales
                # 1. ADB
                adb_ver = (data["adb_version"].splitlines()[0] if data["adb_version"] else "—")
                if "version " in adb_ver.lower():
                    adb_ver = adb_ver.split("version ")[-1].strip()
                adb_srv = "OK" if "en ejecución" in data["adb_server_status"].lower() else "Error"
                self._tile_adb_status.value = f"ADB: {adb_ver}\nServidor: {adb_srv} (P:{data['adb_server_port']})"

                # 2. Red
                phone_ip_str = data["device_ip"] or "No detectada"
                dev_port_str = data["ports_used"].get("device_port", "—")
                self._tile_network.value = f"PC IP: {data['pc_ip']}\nTeléfono: {phone_ip_str}:{dev_port_str}"

                # 3. Dispositivo & SO
                model_str = f"{data['device_manufacturer']} {data['device_model']}".strip()
                if not model_str or model_str == "— —":
                    model_str = data["selected_serial"] or "Sin selección"
                android_str = f"v{data['android_version']} (API {data['sdk_api']})" if data["android_version"] != "—" else "—"
                self._tile_device_info.value = f"Modelo: {model_str}\nAndroid: {android_str} ({data['authorization_status']})"

                # 4. Hardware
                bat_str = f"{data['battery_level']}% ({data['battery_status']})" if data["battery_level"] is not None else "—"
                self._tile_hardware.value = f"Batería: {bat_str}\nPC Libre: {data['storage_pc']}"

                # Formatear informe exhaustivo para la consola
                lines = [
                    "=" * 64,
                    "              INFORME DE DIAGNÓSTICO TÉCNICO",
                    "=" * 64,
                    f"Fecha y hora: {data['timestamp']}",
                    "",
                    "[1] ENTORNO Y SERVIDOR ADB",
                    f"  • Versión ADB       : {data['adb_version']}",
                    f"  • Ruta ejecutable   : {data['adb_path']}",
                    f"  • Estado servidor   : {data['adb_server_status']}",
                    f"  • Puerto local      : {data['adb_server_port']}",
                    f"  • Proceso propietario: {data['adb_server_owner'].get('name') or 'No identificado'} "
                    f"(PID {data['adb_server_owner'].get('pid') or '—'})",
                    f"  • Ruta propietario  : {data['adb_server_owner'].get('path') or 'No disponible'}",
                    "",
                    "[2] DISPOSITIVOS Y AUTORIZACIÓN",
                    f"  • Dispositivo actual: {data['selected_serial'] or 'Ninguno seleccionado'}",
                    f"  • Estado autorización: {data['authorization_status']}",
                    f"  • Total detectados  : {len(data['detected_devices'])}",
                ]
                for d in data["detected_devices"]:
                    lines.append(f"      - Serial: {d['serial']:<24} Estado: {d['state']:<12} Tipo: {d['transport']}")

                lines.extend([
                    "",
                    "[3] RED, MDNS Y PUERTOS",
                    f"  • IP de la PC       : {data['pc_ip']} (todas: {', '.join(data['pc_all_ips']) or '127.0.0.1'})",
                    f"  • IP del dispositivo: {phone_ip_str}",
                    f"  • Puertos utilizados: ADB Local={data['ports_used'].get('adb_server_port')}, Dispositivo={data['ports_used'].get('device_port')} (TLS: {data['ports_used'].get('is_tls')})",
                    f"  • Estado de mDNS    : {data['mdns_status']}",
                    f"  • Último intento    : {data['last_reconnect_issue'].get('stage') or '—'} / "
                    f"{data['last_reconnect_issue'].get('code') or '—'}",
                ])
                if data["mdns_services"]:
                    for s in data["mdns_services"]:
                        lines.append(f"      - Servicio: {s.get('service_name')} -> {s.get('endpoint')}")

                conn = data["connectivity_test"]
                lines.extend([
                    "",
                    "[4] PRUEBA DE CONECTIVIDAD",
                ])
                if conn.get("tested_host"):
                    lines.append(f"  • Destino probado   : {conn['tested_host']}:{conn.get('tested_port')}")
                    lines.append(f"  • Ping ICMP         : {'EXITOSO' if conn.get('icmp_ping_ok') else 'FALLÓ'} - {conn.get('icmp_ping_message')}")
                    lines.append(f"  • Puerto TCP        : {'ABIERTO (Conexión exitosa)' if conn.get('tcp_port_open') else 'CERRADO o NO RESPONDE'} {conn.get('tcp_error') or ''}")
                else:
                    lines.append(f"  • Estado            : {conn.get('status', 'No aplicable')}")

                lines.extend([
                    "",
                    "[5] HARDWARE Y SISTEMA",
                    f"  • Modelo / Marca    : {data['device_manufacturer']} {data['device_model']}",
                    f"  • Versión Android   : {data['android_version']} (Nivel API: {data['sdk_api']}, ABI: {data['cpu_abi']})",
                    f"  • Batería           : {bat_str}",
                    f"  • Almacenamiento PC : {data['storage_pc']}",
                    f"  • Espacio en móvil  : {data['storage_phone']}",
                    "",
                    "[6] RECOMENDACIONES",
                ])
                for rec in data["recommendations"]:
                    lines.append(f"  [!] {rec}")

                lines.append("=" * 64)
                self._set_output("\n".join(lines))
                self._log("Diagnóstico técnico completo completado exitosamente.", theme.OK)
            except Exception as exc:
                logger.exception("Error al ejecutar diagnóstico técnico completo: %s", exc)
                self._set_output(f"Error ejecutando diagnóstico técnico: {exc}")
                self._log(f"Fallo en diagnóstico técnico: {exc}", theme.ERR)
            finally:
                self._diag_progress.visible = False
                try:
                    self._diag_progress.update()
                    self._tile_adb_status.update()
                    self._tile_network.update()
                    self._tile_device_info.update()
                    self._tile_hardware.update()
                except Exception:
                    pass

        threading.Thread(target=work, name="tech-diagnostics", daemon=True).start()

    def _diag_connection(self, _e: ft.ControlEvent) -> None:
        """Ejecuta diagnóstico completo de ADB, LAN, mDNS, conectividad y hardware."""
        self._run_full_technical_diagnostics(_e)

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
