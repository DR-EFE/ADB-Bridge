"""Pestaña «Administrador de Dispositivos»: gestión integral de conexiones USB, Wi-Fi, emparejamiento e historial."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService
from ..services.network_scan_service import NetworkHost, NetworkScanError, NetworkScanResult, NetworkScanService, ScanProfile
from ..services.wireless_qr_service import WirelessQrPairingService

logger = logging.getLogger("ui.devices")


class DevicesPage:
    """Página completa para el Administrador de Dispositivos Android."""

    def __init__(
        self,
        device: DeviceService,
        page: Optional[ft.Page] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_device_changed: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._device = device
        self._page = page
        self._on_log = on_log
        self._on_device_changed = on_device_changed
        self._refresh_lock = threading.Lock()
        self._is_shutdown = False
        self._unlock_credentials: Dict[str, str] = {}
        # El monitor USB -> Wi-Fi corre fuera de esta página.  Este callback
        # actualiza las tarjetas justo al cambiar ADB, sin pulsar Refrescar.
        self._device.add_device_change_listener(self.refresh_devices)

        # ---------------------------------------------------------------------
        # Controles de Estado / Feedback
        # ---------------------------------------------------------------------
        self._status_feedback = ft.Text(
            "Listo. Selecciona una acción o dispositivo.",
            size=12,
            color=theme.TEXT_MUTED,
            selectable=True,
        )
        self._progress = ft.ProgressBar(visible=False, color=theme.ACCENT, height=3)

        # ---------------------------------------------------------------------
        # Contenedores dinámicos
        # ---------------------------------------------------------------------
        self._online_list_col = ft.Column(spacing=8)
        self._saved_list_col = ft.Column(spacing=8)

        # ---------------------------------------------------------------------
        # Descubrimiento de red Nmap (manual; Guardian es opcional)
        # ---------------------------------------------------------------------
        self._network_scanner = NetworkScanService()
        self._network_scan_lock = threading.Lock()
        self._network_scan_cancel = threading.Event()
        self._guardian_stop = threading.Event()
        self._guardian_thread: Optional[threading.Thread] = None
        self._last_network_scan: Optional[NetworkScanResult] = None
        networks = self._network_scanner.detect_local_networks()
        default_network = networks[0].cidr if networks else "192.168.1.0/24"
        self._network_target_field = ft.TextField(
            label="Red IPv4 (CIDR)", hint_text="192.168.1.0/24", value=default_network,
            width=210, dense=True, text_size=13,
        )
        self._network_interface_selector = ft.Dropdown(
            label="Interfaz detectada",
            value=default_network,
            width=250,
            dense=True,
            options=[ft.DropdownOption(key=item.cidr, text=f"{item.name}: {item.cidr}") for item in networks],
            on_select=self._handle_network_interface_select,
        )
        self._network_profile = ft.SegmentedButton(
            selected=[ScanProfile.QUICK.value],
            allow_multiple_selection=False,
            segments=[
                ft.Segment(value=ScanProfile.QUICK.value, label=ft.Text("Rápido")),
                ft.Segment(value=ScanProfile.WIRELESS.value, label=ft.Text("Wireless")),
                ft.Segment(value=ScanProfile.DEEP.value, label=ft.Text("Profundo")),
                ft.Segment(value=ScanProfile.SECURITY.value, label=ft.Text("Seguridad")),
            ],
        )
        self._network_scan_summary = ft.Text("Nmap: comprobando disponibilidad...", size=12, color=theme.TEXT_MUTED, selectable=True)
        self._network_known_col = ft.Column(spacing=6)
        self._network_new_col = ft.Column(spacing=6)
        self._network_changes_col = ft.Column(spacing=4)
        self._network_risks_col = ft.Column(spacing=4)
        self._network_cancel_btn = ft.OutlinedButton("Cancelar escaneo", icon=ft.Icons.CANCEL, visible=False, on_click=self._handle_cancel_network_scan)
        self._guardian_checkbox = ft.Checkbox(label="Guardian (conocidos cada 60 s)", value=False, on_change=self._handle_guardian_change)

        # ---------------------------------------------------------------------
        # Panel de Conectar Nuevo Dispositivo (Expandible)
        # ---------------------------------------------------------------------
        self._new_ip_field = ft.TextField(
            label="Dirección IP o IP:Puerto",
            hint_text="192.168.1.185:5555",
            width=260,
            text_size=13,
            dense=True,
        )
        self._new_name_field = ft.TextField(
            label="Nombre amigable (opcional)",
            hint_text="Mi Celular Personal",
            width=240,
            text_size=13,
            dense=True,
        )

        # Campos para Wireless Debugging Pairing
        self._pair_host_field = ft.TextField(
            label="Host / IP",
            hint_text="192.168.1.185",
            width=180,
            text_size=13,
            dense=True,
        )
        self._pair_port_field = ft.TextField(
            label="Puerto de emparejamiento",
            hint_text="38493",
            width=160,
            text_size=13,
            dense=True,
        )
        self._pair_code_field = ft.TextField(
            label="Código (6 dígitos)",
            hint_text="123456",
            width=140,
            text_size=13,
            dense=True,
        )
        self._qr_service: Optional[WirelessQrPairingService] = None
        self._qr_dialog: Optional[ft.AlertDialog] = None
        self._qr_status = ft.Text("", size=12, color=theme.TEXT_MUTED)
        _fit_contain = getattr(ft, "BoxFit", getattr(ft, "ImageFit", None))
        _fit_val = _fit_contain.CONTAIN if _fit_contain else "contain"
        self._qr_image = ft.Image(src="", width=280, height=280, fit=_fit_val)

        self._connect_mode_btn = ft.SegmentedButton(
            selected=["tcp"],
            allow_multiple_selection=False,
            segments=[
                ft.Segment(value="tcp", label=ft.Text("Conexión Directa TCP/IP (Puerto 5555)"), icon=ft.Icon(ft.Icons.WIFI)),
                ft.Segment(value="pair", label=ft.Text("Vincular con Código (6 dígitos)"), icon=ft.Icon(ft.Icons.PIN)),
                ft.Segment(value="qr", label=ft.Text("Vincular con Código QR"), icon=ft.Icon(ft.Icons.QR_CODE_2)),
            ],
            on_change=self._on_connect_mode_change,
        )

        self._tcp_panel = ft.Row(
            wrap=True,
            spacing=10,
            controls=[
                self._new_ip_field,
                self._new_name_field,
                ft.FilledButton(
                    "Conectar Endpoint",
                    icon=ft.Icons.LINK,
                    on_click=self._handle_connect_direct,
                ),
            ],
        )

        self._pair_panel = ft.Column(
            visible=False,
            spacing=10,
            controls=[
                ft.Text(
                    "En tu celular: Ajustes → Opciones de desarrollador → Depuración inalámbrica → Vincular con código.",
                    size=12,
                    color=theme.TEXT_MUTED,
                ),
                ft.Row(
                    wrap=True,
                    spacing=10,
                    controls=[
                        self._pair_host_field,
                        self._pair_port_field,
                        self._pair_code_field,
                        ft.FilledButton(
                            "Vincular y Conectar",
                            icon=ft.Icons.PHONELINK_LOCK,
                            on_click=self._handle_pair_wireless,
                        ),
                        ft.OutlinedButton(
                            "Generar QR",
                            icon=ft.Icons.QR_CODE_2,
                            on_click=self._handle_pair_wireless_qr,
                        ),
                    ],
                ),
            ],
        )

        self._qr_panel = ft.Column(
            visible=False,
            spacing=10,
            controls=[
                ft.Text(
                    "En tu celular (Android 11+): Ajustes → Opciones de desarrollador → Depuración inalámbrica → Vincular dispositivo con código QR.",
                    size=12,
                    color=theme.TEXT_MUTED,
                ),
                ft.Row(
                    spacing=10,
                    controls=[
                        ft.FilledButton(
                            "Generar y Mostrar Código QR",
                            icon=ft.Icons.QR_CODE_2,
                            bgcolor=theme.OK,
                            color=theme.BG,
                            on_click=self._handle_pair_wireless_qr,
                        ),
                    ],
                ),
            ],
        )

        self._connect_card = ft.Container(
            visible=False,
            content=theme.card(
                ft.Column(
                    spacing=12,
                    controls=[
                        ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.ADD_LINK, color=theme.ACCENT, size=20),
                                        ft.Text("Conectar Nuevo Dispositivo Android", weight=ft.FontWeight.BOLD, size=15),
                                    ],
                                    spacing=8,
                                ),
                                ft.IconButton(
                                    ft.Icons.CLOSE,
                                    tooltip="Cerrar panel",
                                    on_click=lambda _: self._toggle_connect_panel(False),
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        self._connect_mode_btn,
                        self._tcp_panel,
                        self._pair_panel,
                        self._qr_panel,
                    ],
                )
            ),
        )

        # ---------------------------------------------------------------------
        # Panel de Renombrar (Inline Editor)
        # ---------------------------------------------------------------------
        self._rename_id: Optional[str] = None
        self._rename_input = ft.TextField(
            label="Nuevo nombre del dispositivo",
            width=320,
            text_size=13,
            dense=True,
        )
        self._rename_card = ft.Container(
            visible=False,
            content=theme.card(
                ft.Row(
                    spacing=10,
                    controls=[
                        ft.Icon(ft.Icons.EDIT, color=theme.ACCENT, size=20),
                        self._rename_input,
                        ft.FilledButton("Guardar", on_click=self._handle_save_rename),
                        ft.OutlinedButton("Cancelar", on_click=lambda _: self._toggle_rename_panel(False)),
                    ],
                )
            ),
        )

        # ---------------------------------------------------------------------
        # Ficha Técnica / Especificaciones
        # ---------------------------------------------------------------------
        self._specs_col = ft.Column(spacing=6)
        self._specs_card = theme.card(
            ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=theme.OK, size=20),
                            ft.Text("Ficha Técnica y Diagnóstico de Hardware", weight=ft.FontWeight.BOLD, size=14),
                            ft.FilledButton(
                                "Actualizar Ficha",
                                icon=ft.Icons.REFRESH,
                                height=28,
                                on_click=lambda _: self._load_specs(self._device.serial),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self._specs_col,
                ],
            )
        )

        # Hero card del dispositivo activo
        self._hero_active_card = ft.Container()

        # ---------------------------------------------------------------------
        # Construcción de la Vista Principal
        # ---------------------------------------------------------------------
        self.view = ft.Column(
            expand=True,
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                # 1. Barra de Herramientas Principal
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                    controls=[
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.PHONELINK_SETUP_ROUNDED, color=theme.ACCENT, size=24),
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Text("Administrador de Dispositivos", size=18, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                        ft.Text("Conexiones USB y Wi-Fi, emparejamiento rápido y selección activa", size=12, color=theme.TEXT_MUTED),
                                    ],
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Row(
                            spacing=8,
                            wrap=True,
                            controls=[
                                ft.FilledButton(
                                    "Conectar por IP",
                                    icon=ft.Icons.WIFI,
                                    height=34,
                                    on_click=lambda _: self._toggle_connect_panel(True),
                                ),
                                ft.FilledButton(
                                    "Vincular QR",
                                    icon=ft.Icons.QR_CODE_2,
                                    height=34,
                                    bgcolor=theme.OK,
                                    color=theme.BG,
                                    tooltip="Empareja un teléfono Android 11+ escaneando un código QR",
                                    on_click=self._handle_pair_wireless_qr,
                                ),
                                ft.OutlinedButton(
                                    "Refrescar",
                                    icon=ft.Icons.REFRESH,
                                    height=34,
                                    on_click=lambda _: self.refresh_devices(),
                                ),
                                ft.PopupMenuButton(
                                    icon=ft.Icons.MORE_VERT,
                                    tooltip="Opciones avanzadas",
                                    items=[
                                        ft.PopupMenuItem(
                                            content="Escanear mDNS / Wi-Fi",
                                            icon=ft.Icons.SEARCH,
                                            on_click=self._handle_scan_mdns,
                                        ),
                                        ft.PopupMenuItem(
                                            content="Reiniciar servidor ADB",
                                            icon=ft.Icons.RESTART_ALT,
                                            on_click=self._handle_restart_adb,
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                self._progress,
                self._status_feedback,
                # 2. Hero Card: Dispositivo Activo Actual (¡Destacado visualmente!)
                self._hero_active_card,
                # 3. Paneles expandibles
                self._connect_card,
                self._rename_card,

                # 2. Descubrimiento / diagnóstico de red con Nmap
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(ft.Icons.ROUTER_OUTLINED, color=theme.ACCENT, size=20),
                                            ft.Text("Buscar dispositivos en la red", weight=ft.FontWeight.BOLD, size=15),
                                        ],
                                        spacing=8,
                                    ),
                                    self._guardian_checkbox,
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                wrap=True,
                            ),
                            ft.Text(
                                "Descubre hosts y puertos ADB con Nmap. Solo coincidencias exactas por MAC se reconectan automáticamente.",
                                size=12,
                                color=theme.TEXT_MUTED,
                            ),
                            ft.Row(
                                [
                                    self._network_target_field,
                                    self._network_interface_selector,
                                    self._network_profile,
                                    ft.FilledButton("Escanear red", icon=ft.Icons.SEARCH, on_click=self._handle_network_scan),
                                    ft.OutlinedButton("Solo conocidos", icon=ft.Icons.BOOKMARK, on_click=self._handle_known_network_scan),
                                    self._network_cancel_btn,
                                ],
                                spacing=8,
                                wrap=True,
                            ),
                            ft.Row(
                                [
                                    ft.OutlinedButton("Exportar CSV", icon=ft.Icons.TABLE_VIEW, on_click=lambda _: self._export_network_scan("csv")),
                                    ft.OutlinedButton("Exportar JSON", icon=ft.Icons.DATA_OBJECT, on_click=lambda _: self._export_network_scan("json")),
                                    ft.OutlinedButton("Abrir Advanced IP Scanner", icon=ft.Icons.OPEN_IN_NEW, on_click=self._open_advanced_ip_scanner),
                                ],
                                spacing=8,
                                wrap=True,
                            ),
                            self._network_scan_summary,
                            ft.Text("Dispositivos conocidos", weight=ft.FontWeight.BOLD, size=13),
                            self._network_known_col,
                            ft.Text("Candidatos nuevos", weight=ft.FontWeight.BOLD, size=13),
                            self._network_new_col,
                            ft.Text("Cambios desde el escaneo anterior", weight=ft.FontWeight.BOLD, size=13),
                            self._network_changes_col,
                            ft.Text("Diagnóstico y riesgos", weight=ft.FontWeight.BOLD, size=13),
                            self._network_risks_col,
                        ],
                    )
                ),

                # 3. Dispositivos en Línea (Conectados Ahora)
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Row(
                                        [
                                            ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=theme.OK, size=18),
                                            ft.Text("Dispositivos en Línea Detectados (ADB)", weight=ft.FontWeight.BOLD, size=14, color=theme.TEXT),
                                        ],
                                        spacing=8,
                                    ),
                                    ft.Text("Haz clic en 'Usar este dispositivo' para cambiar el teléfono activo", size=11, color=theme.TEXT_MUTED),
                                ],
                            ),
                            self._online_list_col,
                        ],
                    )
                ),

                # 4. Dispositivos Guardados (config/devices.json)
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.BOOKMARK_BORDER, color=theme.WARN, size=18),
                                    ft.Text("Historial de Dispositivos Registrados (devices.json)", weight=ft.FontWeight.BOLD, size=14, color=theme.TEXT),
                                ],
                                spacing=8,
                            ),
                            self._saved_list_col,
                        ],
                    )
                ),

                # 5. Ficha Técnica
                self._specs_card,
            ],
        )

        # Cargar datos iniciales
        self.refresh_devices()

    # -------------------------------------------------------------------------
    # Métodos de Refresco y Renderizado
    # -------------------------------------------------------------------------
    def refresh_devices(self) -> None:
        """Consulta y renderiza los dispositivos online y guardados."""
        if self._is_shutdown or not self._refresh_lock.acquire(blocking=False):
            return
        self._set_busy(True, "Consultando dispositivos ADB y archivo de configuración...")
        threading.Thread(target=self._worker_refresh_guarded, name="devices-refresh", daemon=True).start()

    def _worker_refresh_guarded(self) -> None:
        """Ejecuta una sola actualización a la vez y libera el cerrojo."""
        try:
            self._worker_refresh()
        finally:
            self._refresh_lock.release()

    def _worker_refresh(self) -> None:
        try:
            data = self._device.get_management_data()
            cards = data.get("cards", [])
            saved_items = [
                {
                    "id": item.get("id"),
                    "friendly_name": item.get("friendly_name", "Android"),
                    "usb_serial": item.get("usb_serial"),
                    "last_endpoint": item.get("current_endpoint"),
                    "mode": item.get("mode", "usb"),
                    "last_connected_utc": item.get("last_connected"),
                    "is_online": item.get("state") == "conectado",
                    "is_active": bool(item.get("is_active")),
                    "provisional": bool(item.get("provisional")),
                }
                for item in cards if item.get("is_saved")
            ]
            online_items = []
            status = self._device.get_status()
            for info in status.available_devices:
                online_items.append({
                    "serial": info.serial,
                    "is_network": info.is_network,
                    "is_active": bool(
                        (info.is_primary or info.serial == self._device.serial)
                        and info.state == "device"
                    ),
                    "state": info.state,
                    "model": info.model or info.serial,
                    "friendly_name": info.friendly_name or info.serial,
                    "battery": info.battery,
                    "mode": info.mode,
                })

            # 1. Renderizar lista online
            online_controls = []
            if not online_items:
                online_controls.append(
                    ft.Container(
                        padding=12,
                        content=ft.Text(
                            "No hay dispositivos conectados por USB ni por Wi-Fi actualmente.",
                            size=13,
                            color=theme.TEXT_MUTED,
                            italic=True,
                        ),
                    )
                )
            else:
                for item in online_items:
                    online_controls.append(self._build_online_card(item))

            self._online_list_col.controls = online_controls

            # 2. Renderizar lista guardada
            saved_controls = []
            if not saved_items:
                saved_controls.append(
                    ft.Container(
                        padding=12,
                        content=ft.Text(
                            "No hay dispositivos guardados en devices.json.",
                            size=13,
                            color=theme.TEXT_MUTED,
                            italic=True,
                        ),
                    )
                )
            else:
                for s in saved_items:
                    saved_controls.append(self._build_saved_card(s))

            self._saved_list_col.controls = saved_controls

            # 3. Renderizar Hero del Dispositivo Activo
            active_item = next((item for item in online_items if item["is_active"]), None)
            if not active_item and self._device.serial:
                active_item = next((item for item in online_items if item["serial"] == self._device.serial), None)
            self._render_hero_active_card(active_item, has_any_online=bool(online_items))

            try:
                self._online_list_col.update()
                self._saved_list_col.update()
            except Exception:
                pass

            # 4. Cargar ficha técnica del dispositivo activo
            specs = self._device.get_device_full_specs(self._device.serial)
            self._render_specs(specs)

            self._set_busy(False, f"Actualizado: {len(online_items)} en línea, {len(saved_items)} guardados.")
        except Exception as exc:
            logger.error("Error al refrescar dispositivos: %s", exc)
            self._set_busy(False, f"Error al refrescar dispositivos: {exc}", is_error=True)

    def _render_hero_active_card(self, active_item: Optional[Dict[str, Any]], has_any_online: bool) -> None:
        """Renderiza el banner destacado del dispositivo activo en uso."""
        if active_item:
            title = active_item["friendly_name"]
            serial = active_item["serial"]
            is_net = active_item["is_network"]
            battery = active_item["battery"]
            bat_str = f" • Batería: {battery}%" if battery is not None else ""

            content = ft.Container(
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    wrap=True,
                    controls=[
                        ft.Row(
                            spacing=12,
                            controls=[
                                ft.Container(
                                    content=ft.Icon(
                                        ft.Icons.WIFI if is_net else ft.Icons.USB,
                                        size=28,
                                        color=theme.OK,
                                    ),
                                    bgcolor=theme.OK_LIGHT,
                                    border_radius=10,
                                    padding=10,
                                    border=ft.Border.all(1, theme.OK),
                                ),
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Row(
                                            spacing=6,
                                            controls=[
                                                ft.Container(
                                                    content=ft.Row(
                                                        spacing=4,
                                                        tight=True,
                                                        controls=[
                                                            ft.Icon(ft.Icons.CHECK_CIRCLE, size=12, color=theme.OK),
                                                            ft.Text("DISPOSITIVO ACTIVO EN USO", size=10, weight=ft.FontWeight.BOLD, color=theme.OK),
                                                        ],
                                                    ),
                                                    padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                                    bgcolor=theme.OK_LIGHT,
                                                    border_radius=4,
                                                    border=ft.Border.all(1, theme.OK),
                                                ),
                                                ft.Text(f"Conexión: {'Wi-Fi' if is_net else 'Cable USB'}", size=11, color=theme.TEXT_MUTED),
                                            ],
                                        ),
                                        ft.Text(title, size=17, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                        ft.Text(f"Serial / IP: {serial} • Estado: Listo{bat_str}", size=12, color=theme.TEXT_MUTED),
                                    ],
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=6,
                            controls=[
                                ft.OutlinedButton(
                                    "Ficha Técnica",
                                    icon=ft.Icons.INFO_OUTLINE,
                                    height=32,
                                    on_click=lambda _: self._load_specs(serial),
                                ),
                                ft.OutlinedButton(
                                    "Ping",
                                    icon=ft.Icons.NETWORK_CHECK,
                                    height=32,
                                    visible=is_net,
                                    on_click=lambda _: self._action_ping(serial),
                                ),
                                ft.OutlinedButton(
                                    "Desconectar",
                                    icon=ft.Icons.LINK_OFF,
                                    height=32,
                                    visible=is_net,
                                    on_click=lambda _: self._action_disconnect(serial),
                                ),
                            ],
                        ),
                    ],
                ),
                padding=14,
                bgcolor=theme.ACCENT_LIGHT,
                border_radius=10,
                border=ft.Border.all(1.5, theme.ACCENT),
            )
        else:
            msg = (
                "Hay dispositivos detectados abajo. Pulsa 'Usar este dispositivo' en el que desees controlar."
                if has_any_online
                else "Conecta un teléfono por cable USB o haz clic en 'Conectar por IP' / 'Vincular QR'."
            )
            content = ft.Container(
                content=ft.Row(
                    spacing=12,
                    controls=[
                        ft.Container(
                            content=ft.Icon(ft.Icons.PHONELINK_OFF, size=24, color=theme.WARN),
                            bgcolor=theme.WARN_LIGHT,
                            border_radius=10,
                            padding=10,
                            border=ft.Border.all(1, theme.WARN),
                        ),
                        ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text("Ningún dispositivo activo seleccionado", size=15, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                ft.Text(msg, size=12, color=theme.TEXT_MUTED),
                            ],
                        ),
                    ],
                ),
                padding=14,
                bgcolor=theme.WARN_LIGHT,
                border_radius=10,
                border=ft.Border.all(1, theme.WARN),
            )

        self._hero_active_card.content = content
        try:
            self._hero_active_card.update()
        except Exception:
            pass

    def _build_online_card(self, dev: Dict[str, Any]) -> ft.Control:
        serial = dev["serial"]
        is_net = dev["is_network"]
        is_active = dev["is_active"]
        state = dev["state"]
        model = dev["model"]
        friendly = dev["friendly_name"]
        battery = dev["battery"]

        title = f"{friendly}" if friendly != serial else f"Dispositivo {serial}"
        subtitle = f"Serial / Endpoint: {serial}"
        if model and model != "—" and model not in title:
            subtitle += f" • Modelo: {model}"

        # Badges informativos
        badges = []
        mode_text = "Wi-Fi (TCP/IP)" if is_net else "USB"
        mode_color = theme.OK if is_net else theme.ACCENT
        badges.append(self._badge(mode_text, mode_color))

        if state == "device":
            badges.append(self._badge("Listo", theme.OK))
        elif state == "unauthorized":
            badges.append(self._badge("No Autorizado", theme.WARN, is_bold=True))
        else:
            badges.append(self._badge(state.upper(), theme.ERR))

        if battery is not None:
            badges.append(self._badge(f"{battery}%", theme.TEXT_MUTED))

        # Acciones simplificadas
        actions = []
        if not is_active and state == "device":
            actions.append(
                ft.FilledButton(
                    "Usar este dispositivo",
                    icon=ft.Icons.CHECK,
                    height=32,
                    on_click=lambda _, s=serial: self._action_set_active(s),
                )
            )

        if is_active:
            actions.append(
                ft.Container(
                    content=ft.Row(
                        spacing=4,
                        tight=True,
                        controls=[
                            ft.Icon(ft.Icons.CHECK_CIRCLE, size=14, color=theme.OK),
                            ft.Text("SELECCIONADO", size=11, weight=ft.FontWeight.BOLD, color=theme.OK),
                        ],
                    ),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    border_radius=6,
                    bgcolor=theme.OK_LIGHT,
                    border=ft.Border.all(1, theme.OK),
                )
            )

        if state == "device":
            actions.append(
                ft.OutlinedButton(
                    "Desbloquear",
                    icon=ft.Icons.LOCK_OPEN,
                    height=32,
                    on_click=lambda _, s=serial: self._open_unlock_dialog(s),
                )
            )

        if not is_net and state == "device" and dev.get("mode") == "tcpip-5555":
            actions.append(
                ft.OutlinedButton(
                    "Convertir a Wi-Fi",
                    icon=ft.Icons.WIFI,
                    height=32,
                    on_click=lambda _, s=serial: self._action_convert_wifi(s),
                )
            )

        actions.append(
            ft.IconButton(
                ft.Icons.INFO_OUTLINE,
                tooltip="Ficha técnica",
                icon_size=18,
                on_click=lambda _, s=serial: self._load_specs(s),
            )
        )

        if is_net:
            actions.append(
                ft.IconButton(
                    ft.Icons.NETWORK_CHECK,
                    tooltip="Hacer ping",
                    icon_size=18,
                    on_click=lambda _, s=serial: self._action_ping(s),
                )
            )

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            bgcolor=theme.ACCENT_LIGHT if is_active else theme.SURFACE,
            border_radius=8,
            border=ft.Border.all(1.5 if is_active else 1.0, theme.ACCENT if is_active else theme.BORDER),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
                controls=[
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.Icon(
                                ft.Icons.WIFI if is_net else ft.Icons.USB,
                                color=theme.OK if is_active else theme.TEXT_MUTED,
                                size=24,
                            ),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Row(
                                        spacing=6,
                                        controls=[
                                            ft.Text(title, weight=ft.FontWeight.BOLD, size=14, color=theme.TEXT),
                                        ] + badges,
                                    ),
                                    ft.Text(subtitle, size=11, color=theme.TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    ft.Row(actions, spacing=6),
                ],
            ),
        )

    def _build_saved_card(self, dev: Dict[str, Any]) -> ft.Control:
        dev_id = dev["id"]
        friendly = dev["friendly_name"]
        usb = dev["usb_serial"]
        ep = dev["last_endpoint"]
        mode = dev["mode"]
        last_utc = dev["last_connected_utc"]
        is_online = dev["is_online"]
        is_active = dev["is_active"]

        badges = []
        if is_online:
            badges.append(self._badge("En línea", theme.OK))
        else:
            badges.append(self._badge("Offline", theme.TEXT_MUTED))

        if is_active:
            badges.append(self._badge("ACTIVO", theme.OK, is_bold=True))

        actions = []
        if is_online:
            if not is_active:
                actions.append(
                    ft.FilledButton(
                        "Usar",
                        icon=ft.Icons.CHECK,
                        height=28,
                        on_click=lambda _, i=dev_id: self._action_activate_saved(i),
                    )
                )
            actions.append(
                ft.OutlinedButton(
                    "Desconectar",
                    icon=ft.Icons.LINK_OFF,
                    height=28,
                    on_click=lambda _, i=dev_id: self._action_disconnect(i),
                )
            )
        else:
            actions.append(
                ft.FilledButton(
                    "Conectar",
                    icon=ft.Icons.PLAY_ARROW,
                    height=28,
                    on_click=lambda _, i=dev_id: self._action_activate_saved(i),
                )
            )

        actions.append(
            ft.IconButton(
                ft.Icons.EDIT_OUTLINED,
                tooltip="Renombrar",
                icon_size=16,
                on_click=lambda _, i=dev_id, n=friendly: self._open_rename_panel(i, n),
            )
        )
        actions.append(
            ft.IconButton(
                ft.Icons.DELETE_OUTLINE,
                tooltip="Eliminar del historial",
                icon_size=16,
                icon_color=theme.ERR,
                on_click=lambda _, i=dev_id, n=friendly, s=(ep or usb or "—"): self._action_delete_saved(i, n, s),
            )
        )

        sub_info = f"Endpoint: {ep or '(sin IP)'} • USB: {usb or '—'} • Modo: {mode}"
        if last_utc and last_utc != "—":
            sub_info += f" • Último: {last_utc[:16]}"

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            bgcolor=theme.ACCENT_LIGHT if is_active else theme.SURFACE,
            border_radius=8,
            border=ft.Border.all(1.5 if is_active else 1.0, theme.ACCENT if is_active else theme.BORDER),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
                controls=[
                    ft.Row(
                        spacing=10,
                        controls=[
                            ft.Icon(ft.Icons.PHONE_ANDROID, color=theme.OK if is_online else theme.TEXT_MUTED, size=20),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Row(spacing=6, controls=[ft.Text(friendly, weight=ft.FontWeight.BOLD, size=13, color=theme.TEXT)] + badges),
                                    ft.Text(sub_info, size=11, color=theme.TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    ft.Row(actions, spacing=4),
                ],
            ),
        )

    def _render_specs(self, specs: Dict[str, str]) -> None:
        rows = []
        for k, v in specs.items():
            rows.append(
                ft.Row(
                    spacing=8,
                    controls=[
                        ft.Text(f"{k}:", weight=ft.FontWeight.BOLD, size=12, width=150, color=theme.TEXT_MUTED),
                        ft.Text(str(v), size=13, color=theme.TEXT, selectable=True),
                    ],
                )
            )
        self._specs_col.controls = rows
        try:
            self._specs_card.update()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Acciones de Dispositivos (Workers Asíncronos)
    # -------------------------------------------------------------------------
    def _open_unlock_dialog(self, serial: str) -> None:
        page = self._page or getattr(self.view, "page", None)
        if not page:
            return
        field = ft.TextField(
            label="PIN o contraseña",
            value=self._unlock_credentials.get(serial, ""),
            password=True,
            can_reveal_password=True,
            autofocus=True,
            width=330,
        )

        def submit(_event):
            credential = field.value or ""
            if not credential:
                self._set_busy(False, "Introduce el PIN o la contraseña.", is_error=True)
                return
            self._unlock_credentials[serial] = credential
            field.value = ""
            page.pop_dialog()
            self._set_busy(True, f"Desbloqueando {serial}...")

            def worker():
                result = self._device.unlock_device(serial, credential)
                message = result.message or result.error or "No se pudo desbloquear."
                self._log(message, theme.OK if result.success else theme.ERR)
                self._set_busy(False, message, is_error=not result.success)

            threading.Thread(target=worker, name="dev-unlock", daemon=True).start()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Desbloquear dispositivo"),
            content=ft.Column([
                ft.Text(f"Conexión seleccionada: {serial}", size=12, color=theme.TEXT_MUTED),
                field,
                ft.Text("Se detectará PIN primero y, si no aparece, contraseña. El dato se conserva solo durante esta sesión.",
                        size=11, color=theme.TEXT_MUTED),
            ], tight=True, width=380),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton("Desbloquear", on_click=submit),
            ],
        ))

    def _action_set_active(self, serial: str) -> None:
        self._set_busy(True, f"Cambiando dispositivo activo a {serial}...")

        def worker():
            ok, msg = self._device.set_active_by_serial(serial)
            self._log(msg, theme.OK if ok else theme.WARN)
            self._set_busy(False, msg, is_error=not ok)
            if ok and self._on_device_changed:
                try:
                    self._on_device_changed(self._device.serial or "")
                except Exception:
                    pass
            self.refresh_devices()

        threading.Thread(target=worker, name="dev-set-active", daemon=True).start()

    def _action_activate_saved(self, device_id: str, *, extended: bool = False, allow_cross_network: bool = False) -> None:
        self._set_busy(True, "Reconectando y activando dispositivo guardado...")

        def worker():
            ok, msg = self._device.set_active_by_saved_id(device_id, retry_seconds=30.0, allow_cross_network=allow_cross_network)
            self._log(msg, theme.OK if ok else theme.WARN)
            self._set_busy(False, msg, is_error=not ok)
            if ok and self._on_device_changed:
                try:
                    self._on_device_changed(self._device.serial or "")
                except Exception:
                    pass
            self.refresh_devices()
            if not ok:
                page = self._page or getattr(self.view, "page", None)
                if page:
                    actions = [ft.TextButton("Endpoint manual", on_click=lambda _: page.pop_dialog())]
                    if not extended:
                        actions.extend([
                            ft.TextButton("Probar historial de otra red", on_click=lambda _: (page.pop_dialog(), self._action_activate_saved(device_id, extended=True, allow_cross_network=True))),
                            ft.FilledButton("Intentar 30 s más", on_click=lambda _: (page.pop_dialog(), self._action_activate_saved(device_id, extended=True))),
                        ])
                    dialog = ft.AlertDialog(
                        modal=True,
                        title=ft.Text("Reconexión pendiente"),
                        content=ft.Text(f"{msg}\nPuedes introducir el endpoint manualmente."),
                        actions=actions,
                    )
                    page.show_dialog(dialog)

        threading.Thread(target=worker, name="dev-activate-saved", daemon=True).start()

    def _confirm_provisional(self, device_id: str) -> None:
        page = self._page or getattr(self.view, "page", None)
        if not page:
            return

        def confirm(_event):
            page.pop_dialog()
            result = self._device.confirm_provisional_identity(device_id)
            self._log(result.message or result.error or "No se pudo vincular el teléfono.",
                      theme.OK if result.success else theme.ERR)
            self.refresh_devices()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Confirmar identidad"),
            content=ft.Text("Confirma que el endpoint provisional pertenece a este teléfono. Se guardará su identidad para futuras reconexiones."),
            actions=[ft.TextButton("Cancelar", on_click=lambda _: page.pop_dialog()),
                     ft.FilledButton("Confirmar vínculo", on_click=confirm)],
        ))

    def _action_convert_wifi(self, serial: str) -> None:
        self._set_busy(True, f"Convirtiendo conexión USB {serial} a Wi-Fi (puerto 5555)...")

        def worker():
            ok, msg = self._device.convert_usb_to_wifi(usb_serial=serial)
            self._log(msg, theme.OK if ok else theme.ERR)
            self._set_busy(False, msg, is_error=not ok)
            self.refresh_devices()

        threading.Thread(target=worker, name="dev-convert-wifi", daemon=True).start()

    def _action_ping(self, endpoint: str) -> None:
        self._set_busy(True, f"Enviando paquetes ping ICMP a {endpoint}...")

        def worker():
            ok, msg = self._device.ping_device(endpoint)
            self._log(msg, theme.OK if ok else theme.WARN)
            self._set_busy(False, msg, is_error=not ok)

        threading.Thread(target=worker, name="dev-ping", daemon=True).start()

    def _action_set_primary(self, device_id: str) -> None:
        self._set_busy(True, "Estableciendo dispositivo como principal...")

        def worker():
            res = self._device.set_primary_device(device_id)
            self._log(res.message, theme.OK if res.success else theme.WARN)
            self._set_busy(False, res.message, is_error=not res.success)
            if res.success and self._on_device_changed:
                try:
                    self._on_device_changed(self._device.serial or "")
                except Exception:
                    pass
            self.refresh_devices()

        threading.Thread(target=worker, name="dev-set-primary", daemon=True).start()

    def _action_disconnect(self, device_id: str) -> None:
        self._set_busy(True, "Desconectando dispositivo...")

        def worker():
            res = self._device.disconnect_device(device_id)
            self._log(res.message, theme.OK if res.success else theme.ERR)
            self._set_busy(False, res.message, is_error=not res.success)
            self.refresh_devices()

        threading.Thread(target=worker, name="dev-disconnect", daemon=True).start()

    def _action_delete_saved(self, device_id: str, friendly_name: str, serial: str) -> None:
        page = getattr(self.view, "page", None)
        if not page:
            res = self._device.remove_device(device_id)
            self._log(res.message, theme.OK if res.success else theme.WARN)
            self._set_busy(False, res.message, is_error=not res.success)
            self.refresh_devices()
            return

        def close_dialog(_e=None):
            try:
                page.pop_dialog()
            except Exception:
                pass

        def confirm_remove(_e):
            close_dialog()
            self._set_busy(True, f"Eliminando {friendly_name}...")

            def worker():
                res = self._device.remove_device(device_id)
                self._log(res.message, theme.OK if res.success else theme.ERR)
                self._set_busy(False, res.message, is_error=not res.success)
                self.refresh_devices()

            threading.Thread(target=worker, name="dev-remove", daemon=True).start()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Confirmar eliminación"),
            content=ft.Column(
                [
                    ft.Text(f"¿Estás seguro de eliminar '{friendly_name}'?", weight=ft.FontWeight.BOLD),
                    ft.Text(f"Serial / Endpoint: {serial}", size=12, color=theme.TEXT_MUTED),
                    ft.Container(height=6),
                    ft.Text(
                        "Nota: Se desconectará el endpoint y se detendrá el supervisor. No se desinstalará la APK auxiliar ni se borrarán datos del teléfono.",
                        size=11,
                        color=theme.TEXT_MUTED,
                    ),
                ],
                tight=True,
                width=380,
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=close_dialog),
                ft.FilledButton(
                    "Eliminar definitivamente",
                    style=ft.ButtonStyle(bgcolor=theme.ERR),
                    on_click=confirm_remove,
                ),
            ],
        )
        page.show_dialog(dialog)

    def _load_specs(self, serial: Optional[str]) -> None:
        if not serial:
            self._set_busy(False, "No hay ningún serial para consultar ficha técnica.", is_error=True)
            return
        self._set_busy(True, f"Leyendo ficha técnica de {serial}...")

        def worker():
            specs = self._device.get_device_full_specs(serial)
            self._render_specs(specs)
            self._set_busy(False, f"Ficha técnica cargada para {serial}.")

        threading.Thread(target=worker, name="dev-specs", daemon=True).start()

    # -------------------------------------------------------------------------
    # Operaciones Globales: Conectar Nuevo / Renombrar / mDNS / Restart ADB
    # -------------------------------------------------------------------------
    def _toggle_connect_panel(self, visible: bool) -> None:
        self._connect_card.visible = visible
        try:
            self._connect_card.update()
        except Exception:
            pass

    def _on_connect_mode_change(self, e: ft.ControlEvent) -> None:
        val = next(iter(self._connect_mode_btn.selected), "tcp")
        self._tcp_panel.visible = (val == "tcp")
        self._pair_panel.visible = (val == "pair")
        self._qr_panel.visible = (val == "qr")
        try:
            self._connect_card.update()
        except Exception:
            pass

    def _handle_connect_direct(self, _e: ft.ControlEvent) -> None:
        ip = self._new_ip_field.value or ""
        name = self._new_name_field.value or ""
        if not ip.strip():
            self._set_busy(False, "Introduce una dirección IP válida (ej. 192.168.1.185:5555).", is_error=True)
            return

        self._set_busy(True, f"Conectando a {ip}...")

        def worker():
            ok, msg = self._device.connect_endpoint(ip, friendly_name=name)
            self._log(msg, theme.OK if ok else theme.ERR)
            self._set_busy(False, msg, is_error=not ok)
            if ok:
                self._new_ip_field.value = ""
                self._new_name_field.value = ""
                self._toggle_connect_panel(False)
                self.refresh_devices()

        threading.Thread(target=worker, name="dev-conn-direct", daemon=True).start()

    def _handle_pair_wireless(self, _e: ft.ControlEvent) -> None:
        host = self._pair_host_field.value or ""
        port = self._pair_port_field.value or ""
        code = self._pair_code_field.value or ""
        name = self._new_name_field.value or ""

        if not host.strip() or not port.strip() or not code.strip():
            self._set_busy(False, "Todos los campos de emparejamiento son obligatorios.", is_error=True)
            return

        try:
            num_port = int(port.strip())
        except ValueError:
            self._set_busy(False, "El puerto debe ser un número entero.", is_error=True)
            return

        self._set_busy(True, f"Emparejando con {host}:{num_port}...")

        def worker():
            ok, msg = self._device.pair_wireless(host, num_port, code, friendly_name=name)
            self._log(msg, theme.OK if ok else theme.ERR)
            self._set_busy(False, msg, is_error=not ok)
            if ok:
                self._pair_code_field.value = ""
                self._toggle_connect_panel(False)
                self.refresh_devices()

        threading.Thread(target=worker, name="dev-pair-wireless", daemon=True).start()

    def _handle_pair_wireless_qr(self, _e: ft.ControlEvent) -> None:
        """Muestra el QR estándar y ejecuta pairing/conexión en segundo plano."""
        page = self._page or getattr(self.view, "page", None)
        if not page:
            self._set_busy(False, "La interfaz aún no está conectada a una ventana.", is_error=True)
            return
        try:
            service, payload = self._device.create_qr_payload()
        except Exception as exc:
            self._set_busy(False, str(exc), is_error=True)
            return
        self._qr_service = service
        self._qr_status.value = "QR generado. En el teléfono abre Depuración inalámbrica → Vincular con código QR."
        self._qr_status.color = theme.WARN
        self._qr_image.src = payload.image_base64
        if hasattr(self._qr_image, "src_base64"):
            try:
                self._qr_image.src_base64 = payload.image_base64
            except Exception:
                pass

        def close_dialog(_e=None):
            if self._qr_service:
                self._qr_service.cancel()
            try:
                page.pop_dialog()
            except Exception:
                pass

        def start_pair(_e=None):
            self._qr_status.value = "Esperando que el teléfono escanee el QR…"
            self._qr_status.color = theme.WARN
            self._qr_status.update()
            self._set_busy(True, "Emparejamiento QR en curso…")

            def progress(message: str) -> None:
                self._qr_status.value = message
                try:
                    self._qr_status.update()
                except Exception:
                    pass

            def worker():
                ok, message = self._device.pair_wireless_qr(
                    friendly_name=self._new_name_field.value or "", progress=progress,
                    qr_service=self._qr_service, qr_payload=payload,
                )
                self._log(message, theme.OK if ok else theme.ERR)
                self._set_busy(False, message, is_error=not ok)
                if ok:
                    self._qr_status.value = "Conectado correctamente."
                    self._qr_status.color = theme.OK
                    try:
                        page.pop_dialog()
                    except Exception:
                        pass
                    self.refresh_devices()
                else:
                    self._qr_status.value = message
                    self._qr_status.color = theme.ERR
                    self._qr_status.update()

            threading.Thread(target=worker, name="dev-pair-wireless-qr", daemon=True).start()

        self._qr_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Vincular Wireless Debugging mediante QR", weight=ft.FontWeight.BOLD),
            content=ft.Container(
                width=380,
                content=ft.Column(
                    [
                        self._qr_image,
                        ft.Container(
                            padding=10,
                            border_radius=8,
                            bgcolor=ft.Colors.with_opacity(0.12, theme.WARN),
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=theme.WARN, size=18),
                                            ft.Text("No uses la cámara normal ni Google Lens", weight=ft.FontWeight.BOLD, size=12, color=theme.WARN),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.Text(
                                        "Si usas la cámara normal, tu celular creerá que es una red Wi-Fi y no se conectará.",
                                        size=11,
                                        color=theme.TEXT,
                                    ),
                                    ft.Text(
                                        "Debes escanearlo desde:\nAjustes → Opciones de desarrollador → Depuración inalámbrica → 'Vincular dispositivo con código QR'.",
                                        size=11,
                                        weight=ft.FontWeight.W_500,
                                        color=theme.TEXT,
                                    ),
                                ],
                                spacing=4,
                            ),
                        ),
                        self._qr_status,
                    ],
                    tight=True,
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=close_dialog),
            ],
        )
        page.show_dialog(self._qr_dialog)
        # El servidor empieza a esperar inmediatamente: el usuario solo debe
        # pulsar "Generar QR" y escanearlo desde el teléfono.
        start_pair()

    def _open_rename_panel(self, device_id: str, current_name: str) -> None:
        self._rename_id = device_id
        self._rename_input.value = current_name
        self._rename_card.visible = True
        try:
            self._rename_card.update()
        except Exception:
            pass

    def _toggle_rename_panel(self, visible: bool) -> None:
        self._rename_card.visible = visible
        self._rename_id = None
        try:
            self._rename_card.update()
        except Exception:
            pass

    def _handle_save_rename(self, _e: ft.ControlEvent) -> None:
        if not self._rename_id:
            return
        new_name = self._rename_input.value or ""
        result = self._device.rename_device(self._rename_id, new_name)
        # DeviceService devuelve OperationResult (no una tupla). Usar el
        # mensaje de error cuando la operación falla evita el TypeError del
        # handler de Flet y conserva feedback útil para el usuario.
        ok = result.success
        msg = result.message or result.error or ("Dispositivo renombrado." if ok else "No se pudo renombrar el dispositivo.")
        self._log(msg, theme.OK if ok else theme.WARN)
        self._set_busy(False, msg, is_error=not ok)
        self._toggle_rename_panel(False)
        self.refresh_devices()

    def _handle_scan_mdns(self, _e: ft.ControlEvent) -> None:
        self._set_busy(True, "Escaneando servicios mDNS adb-tls-connect en la red...")

        def worker():
            try:
                services = self._device.find_wireless_devices()
                if not services:
                    msg = "No se encontraron teléfonos anunciados por mDNS en esta red local."
                    self._log(msg, theme.WARN)
                    self._set_busy(False, msg, is_error=True)
                else:
                    endpoints = ", ".join(s["endpoint"] for s in services)
                    msg = f"Encontrados {len(services)} dispositivo(s) mDNS: {endpoints}"
                    self._log(msg, theme.OK)
                    self._set_busy(False, msg)
            except Exception as exc:
                msg = f"Error al escanear mDNS: {exc}"
                self._log(msg, theme.ERR)
                self._set_busy(False, msg, is_error=True)

        threading.Thread(target=worker, name="dev-mdns-scan", daemon=True).start()

    def _handle_restart_adb(self, _e: ft.ControlEvent) -> None:
        self._set_busy(True, "Reiniciando servidor ADB local...")

        def worker():
            ok, msg = self._device.restart_adb()
            self._log(msg, theme.OK if ok else theme.ERR)
            self._set_busy(False, msg, is_error=not ok)
            self.refresh_devices()

        threading.Thread(target=worker, name="dev-restart-adb", daemon=True).start()

    # -------------------------------------------------------------------------
    # Descubrimiento de red Nmap
    # -------------------------------------------------------------------------
    def _selected_network_profile(self) -> ScanProfile:
        selected = list(self._network_profile.selected or [])
        try:
            return ScanProfile(selected[0] if selected else ScanProfile.QUICK.value)
        except ValueError:
            return ScanProfile.QUICK

    def _handle_network_interface_select(self, _e: ft.ControlEvent) -> None:
        selected = (self._network_interface_selector.value or "").strip()
        if selected:
            self._network_target_field.value = selected
            try:
                self._network_target_field.update()
            except Exception:
                pass

    def _handle_network_scan(self, _e: ft.ControlEvent) -> None:
        self._start_network_scan(self._selected_network_profile(), guardian=False)

    def _handle_known_network_scan(self, _e: ft.ControlEvent) -> None:
        self._start_network_scan(ScanProfile.KNOWN, guardian=False)

    def _handle_cancel_network_scan(self, _e: ft.ControlEvent) -> None:
        self._network_scan_cancel.set()
        self._network_scanner.cancel_active_scan()
        self._network_scan_summary.value = "Cancelando escaneo de red..."
        try:
            self._network_scan_summary.update()
        except Exception:
            pass

    def _start_network_scan(self, profile: ScanProfile, *, guardian: bool) -> None:
        if self._is_shutdown or not self._network_scan_lock.acquire(blocking=False):
            if not guardian:
                self._log("Ya hay un escaneo de red en curso.", theme.WARN)
            return
        target = (self._network_target_field.value or "").strip()
        self._network_scan_cancel.clear()
        if not guardian:
            self._set_busy(True, f"Ejecutando escaneo {profile.value} con Nmap...")
            self._network_cancel_btn.visible = True
            try:
                self._network_cancel_btn.update()
            except Exception:
                pass

        def worker() -> None:
            try:
                saved = [record.to_dict() for record in self._device.list_saved_devices()]
                try:
                    mdns_services = self._device.find_wireless_devices()
                except Exception as exc:
                    mdns_services = []
                    logger.debug("mDNS no disponible durante escaneo Nmap: %s", exc)
                result = self._network_scanner.scan(
                    profile,
                    target,
                    saved,
                    mdns_services=mdns_services,
                    cancel_event=self._network_scan_cancel,
                )
                self._last_network_scan = result
                if not guardian and not result.cancelled:
                    self._auto_connect_known_scan_results(result)
                self._render_network_scan(result)
                message = "Escaneo cancelado." if result.cancelled else f"Escaneo terminado: {len(result.hosts)} host(s) detectado(s)."
                if not guardian:
                    self._log(message, theme.WARN if result.cancelled else theme.OK)
                    self._set_busy(False, message, is_error=result.cancelled)
                    self.refresh_devices()
            except NetworkScanError as exc:
                message = f"No se pudo ejecutar Nmap: {exc}"
                self._render_network_error(message)
                if not guardian:
                    self._log(message, theme.ERR)
                    self._set_busy(False, message, is_error=True)
            except Exception as exc:
                logger.exception("Error inesperado al escanear red")
                message = f"Error inesperado durante el escaneo de red: {exc}"
                self._render_network_error(message)
                if not guardian:
                    self._log(message, theme.ERR)
                    self._set_busy(False, message, is_error=True)
            finally:
                self._network_scan_lock.release()
                if not guardian:
                    self._network_cancel_btn.visible = False
                    try:
                        self._network_cancel_btn.update()
                    except Exception:
                        pass

        threading.Thread(target=worker, name=f"network-scan-{profile.value}", daemon=True).start()

    def _auto_connect_known_scan_results(self, result: NetworkScanResult) -> None:
        """Reconecta exclusivamente coincidencias Nmap-MAC exactas.

        ``NetworkScanService`` marca como automáticos únicamente los matches
        de MAC. El servicio ADB valida de nuevo la identidad antes de escribir
        el endpoint en el inventario.
        """
        for host in result.hosts:
            if not host.auto_connect or not host.known_device_id:
                continue
            endpoint = next(iter(host.endpoints), None)
            if not endpoint:
                continue
            connected = self._device.connect_discovered_endpoint(
                host.known_device_id,
                endpoint,
                activate=False,
                trusted_mac_match=host.match_type == "mac",
                discovered_mac=host.mac,
            )
            if connected.success:
                host.changes.append(f"Reconectado automáticamente: {endpoint}")
            else:
                host.risks.append(connected.message or connected.error or "No se pudo validar la reconexión ADB.")

    def _network_host_card(self, host: NetworkHost) -> ft.Control:
        ports = ", ".join(port.label for port in host.ports) or "Sin puertos reportados"
        title = host.known_device_name or host.hostname or host.ip
        identity = f"IP: {host.ip}"
        if host.mac:
            identity += f" | MAC: {host.mac}"
        if host.vendor:
            identity += f" | {host.vendor}"
        if host.latency_ms is not None:
            identity += f" | {host.latency_ms:.1f} ms"
        badges = [self._badge(host.match_type.upper(), theme.OK if host.match_type == "mac" else theme.WARN)]
        if host.adb_candidate:
            badges.append(self._badge("ADB / Wireless", theme.ACCENT))
        actions = []
        endpoint = next(iter(host.endpoints), None)
        if endpoint and host.known_device_id:
            actions.append(ft.OutlinedButton("Conectar", icon=ft.Icons.LINK, height=30,
                                             on_click=lambda _, h=host, ep=endpoint: self._connect_scanned_host(h, ep)))
        elif endpoint:
            actions.append(ft.FilledButton("Agregar y conectar", icon=ft.Icons.ADD_LINK, height=30,
                                           on_click=lambda _, h=host, ep=endpoint: self._connect_new_scanned_endpoint(h, ep)))
        return ft.Container(
            padding=10,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            content=ft.Column(
                spacing=5,
                controls=[
                    ft.Row([ft.Text(title, weight=ft.FontWeight.BOLD, size=13), *badges], spacing=6, wrap=True),
                    ft.Text(identity, size=11, color=theme.TEXT_MUTED, selectable=True),
                    ft.Text(ports, size=11, color=theme.TEXT_MUTED, selectable=True),
                    ft.Row(actions, spacing=6, wrap=True) if actions else ft.Container(),
                ],
            ),
        )

    def _render_network_scan(self, result: NetworkScanResult) -> None:
        known = [host for host in result.hosts if host.known_device_id]
        new = [host for host in result.hosts if not host.known_device_id]
        self._network_known_col.controls = [self._network_host_card(host) for host in known] or [ft.Text("No se detectaron dispositivos conocidos.", size=12, color=theme.TEXT_MUTED)]
        self._network_new_col.controls = [self._network_host_card(host) for host in new] or [ft.Text("No se detectaron candidatos nuevos.", size=12, color=theme.TEXT_MUTED)]
        self._network_changes_col.controls = [ft.Text(change, size=12, color=theme.TEXT_MUTED) for change in result.changes] or [ft.Text("Sin cambios respecto al escaneo anterior.", size=12, color=theme.TEXT_MUTED)]
        risks = [f"{host.ip}: {risk}" for host in result.hosts for risk in host.risks]
        risks.extend(result.warnings)
        self._network_risks_col.controls = [ft.Text(risk, size=12, color=theme.WARN, selectable=True) for risk in risks] or [ft.Text("Sin riesgos ni diagnósticos adicionales.", size=12, color=theme.TEXT_MUTED)]
        source = "cancelado" if result.cancelled else "completado"
        self._network_scan_summary.value = f"Nmap {source}: {len(result.hosts)} hosts | perfil {result.profile} | destino {result.target}"
        for control in (self._network_known_col, self._network_new_col, self._network_changes_col, self._network_risks_col, self._network_scan_summary):
            try:
                control.update()
            except Exception:
                pass

    def _render_network_error(self, message: str) -> None:
        self._network_scan_summary.value = message
        self._network_risks_col.controls = [ft.Text(message, size=12, color=theme.ERR, selectable=True)]
        for control in (self._network_scan_summary, self._network_risks_col):
            try:
                control.update()
            except Exception:
                pass

    def _connect_scanned_host(self, host: NetworkHost, endpoint: str) -> None:
        if not host.known_device_id:
            return
        self._set_busy(True, f"Validando {endpoint} contra el inventario ADB...")

        def worker() -> None:
            result = self._device.connect_discovered_endpoint(
                host.known_device_id or "",
                endpoint,
                activate=False,
                trusted_mac_match=host.match_type == "mac",
                discovered_mac=host.mac,
            )
            message = result.message or result.error or "Conexión finalizada."
            self._log(message, theme.OK if result.success else theme.WARN)
            self._set_busy(False, message, is_error=not result.success)
            self.refresh_devices()

        threading.Thread(target=worker, name="connect-scanned-adb", daemon=True).start()

    def _connect_new_scanned_endpoint(self, host: NetworkHost, endpoint: str) -> None:
        self._set_busy(True, f"Conectando manualmente a {endpoint}...")

        def worker() -> None:
            ok, message = self._device.connect_endpoint(endpoint, wifi_mac_address=host.mac)
            self._log(message, theme.OK if ok else theme.WARN)
            self._set_busy(False, message, is_error=not ok)
            self.refresh_devices()

        threading.Thread(target=worker, name="connect-new-scanned-adb", daemon=True).start()

    def _export_network_scan(self, fmt: str) -> None:
        if not self._last_network_scan:
            self._render_network_error("Primero ejecuta un escaneo antes de exportar resultados.")
            return
        stamp = self._last_network_scan.started_utc.replace(":", "-").replace("+", "_")[:19]
        base = Path(os.environ.get("USERPROFILE") or str(Path.home())) / "AndroidTools" / "exports"
        path = base / f"network-scan-{stamp}.{fmt}"
        try:
            written = self._network_scanner.export(self._last_network_scan, path, fmt)
            message = f"Resultado exportado a {written}"
            self._log(message, theme.OK)
            self._network_scan_summary.value = message
            self._network_scan_summary.update()
        except (OSError, NetworkScanError) as exc:
            self._render_network_error(f"No se pudo exportar: {exc}")

    def _open_advanced_ip_scanner(self, _e: ft.ControlEvent) -> None:
        executable = self._network_scanner.find_advanced_ip_scanner()
        if not executable:
            self._render_network_error("Advanced IP Scanner no está instalado o no se encontró su ejecutable.")
            return
        try:
            subprocess.Popen([executable], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._log("Advanced IP Scanner abierto como herramienta externa.", theme.OK)
        except OSError as exc:
            self._render_network_error(f"No se pudo abrir Advanced IP Scanner: {exc}")

    def _handle_guardian_change(self, _e: ft.ControlEvent) -> None:
        enabled = bool(self._guardian_checkbox.value)
        if not enabled:
            self._guardian_stop.set()
            return
        if self._guardian_thread and self._guardian_thread.is_alive():
            return
        self._guardian_stop.clear()

        def guardian() -> None:
            while not self._guardian_stop.wait(60.0):
                self._start_network_scan(ScanProfile.KNOWN, guardian=True)

        self._guardian_thread = threading.Thread(target=guardian, name="network-guardian", daemon=True)
        self._guardian_thread.start()
        self._log("Guardian activado: comprobará dispositivos conocidos cada 60 segundos.", theme.OK)

    # -------------------------------------------------------------------------
    # Helpers Visuales
    # -------------------------------------------------------------------------
    def _badge(self, text: str, color: str, is_bold: bool = False) -> ft.Container:
        return ft.Container(
            bgcolor=theme.SURFACE_ALT,
            border=ft.Border.all(1, color),
            border_radius=4,
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            content=ft.Text(text, size=10, color=color, weight=ft.FontWeight.BOLD if is_bold else ft.FontWeight.NORMAL),
        )

    def _set_busy(self, busy: bool, message: str, is_error: bool = False) -> None:
        self._progress.visible = busy
        self._status_feedback.value = message
        self._status_feedback.color = theme.ERR if is_error else (theme.WARN if busy else theme.TEXT_MUTED)
        try:
            self._progress.update()
            self._status_feedback.update()
        except Exception:
            pass

    def _log(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)

    def shutdown(self) -> None:
        """Cierre de la página."""
        self._is_shutdown = True
        self._unlock_credentials.clear()
        self._guardian_stop.set()
        self._network_scan_cancel.set()
        self._network_scanner.cancel_active_scan()
        self._device.remove_device_change_listener(self.refresh_devices)
