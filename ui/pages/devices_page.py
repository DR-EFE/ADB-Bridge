"""Pestaña «Administrador de Dispositivos»: gestión integral de conexiones USB, Wi-Fi, emparejamiento e historial."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService
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

        # ---------------------------------------------------------------------
        # Construcción de la Vista Principal
        # ---------------------------------------------------------------------
        self.view = ft.Column(
            expand=True,
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                # 1. Barra de Herramientas Principal
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                    controls=[
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.PHONELINK_SETUP_ROUNDED, color=theme.ACCENT, size=26),
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Text("Administrador de Dispositivos", size=18, weight=ft.FontWeight.BOLD),
                                        ft.Text("Gestión de conexiones USB y Wi-Fi, emparejamiento mDNS e historial", size=12, color=theme.TEXT_MUTED),
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
                                    "Refrescar",
                                    icon=ft.Icons.REFRESH,
                                    on_click=lambda _: self.refresh_devices(),
                                ),
                                ft.FilledButton(
                                    "Vincular con QR",
                                    icon=ft.Icons.QR_CODE_2,
                                    bgcolor=theme.OK,
                                    color=theme.BG,
                                    tooltip="Empareja un teléfono Android 11+ escaneando un código QR",
                                    on_click=self._handle_pair_wireless_qr,
                                ),
                                ft.FilledButton(
                                    "Conectar Nuevo",
                                    icon=ft.Icons.ADD_TO_QUEUE,
                                    bgcolor=theme.ACCENT,
                                    color=theme.BG,
                                    on_click=lambda _: self._toggle_connect_panel(True),
                                ),
                                ft.OutlinedButton(
                                    "Escanear mDNS",
                                    icon=ft.Icons.SEARCH,
                                    on_click=self._handle_scan_mdns,
                                ),
                                ft.OutlinedButton(
                                    "Reiniciar ADB",
                                    icon=ft.Icons.RESTART_ALT,
                                    tooltip="Fuerza un reinicio limpio del daemon ADB en puerto 5037",
                                    on_click=self._handle_restart_adb,
                                ),
                            ],
                        ),
                    ],
                ),
                self._progress,
                self._status_feedback,
                self._connect_card,
                self._rename_card,

                # 2. Dispositivos en Línea (Conectados Ahora)
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=theme.OK, size=20),
                                    ft.Text("Dispositivos en Línea (ADB)", weight=ft.FontWeight.BOLD, size=15),
                                ],
                                spacing=8,
                            ),
                            ft.Text(
                                "Dispositivos reconocidos activamente por el servidor ADB local:",
                                size=12,
                                color=theme.TEXT_MUTED,
                            ),
                            self._online_list_col,
                        ],
                    )
                ),

                # 3. Dispositivos Guardados (config/devices.json)
                theme.card(
                    ft.Column(
                        spacing=10,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.BOOKMARK_BORDER, color=theme.WARN, size=20),
                                    ft.Text("Historial de Dispositivos Registrados (devices.json)", weight=ft.FontWeight.BOLD, size=15),
                                ],
                                spacing=8,
                            ),
                            ft.Text(
                                "Perfiles recordados para reconexión rápida sin necesidad de volver a ingresar datos:",
                                size=12,
                                color=theme.TEXT_MUTED,
                            ),
                            self._saved_list_col,
                        ],
                    )
                ),

                # 4. Ficha Técnica
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
        self._set_busy(True, "Consultando dispositivos ADB y archivo de configuración...")
        threading.Thread(target=self._worker_refresh, name="devices-refresh", daemon=True).start()

    def _worker_refresh(self) -> None:
        try:
            data = self._device.get_management_data()
            online_items = data.get("online_devices", [])
            saved_items = data.get("saved_devices", [])

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

            # 3. Cargar ficha técnica del dispositivo activo
            specs = self._device.get_device_full_specs(self._device.serial)
            self._render_specs(specs)

            self._set_busy(False, f"Actualizado: {len(online_items)} en línea, {len(saved_items)} guardados.")
        except Exception as exc:
            logger.error("Error al refrescar dispositivos: %s", exc)
            self._set_busy(False, f"Error al refrescar dispositivos: {exc}", is_error=True)

    def _build_online_card(self, dev: Dict[str, Any]) -> ft.Control:
        serial = dev["serial"]
        is_net = dev["is_network"]
        is_active = dev["is_active"]
        state = dev["state"]
        model = dev["model"]
        friendly = dev["friendly_name"]
        battery = dev["battery"]

        # Badges
        badges = []
        # Modo
        mode_text = "Wi-Fi (TCP/IP)" if is_net else "USB"
        mode_color = theme.OK if is_net else theme.ACCENT
        badges.append(self._badge(mode_text, mode_color))

        # Estado
        if state == "device":
            badges.append(self._badge("Listo (device)", theme.OK))
        elif state == "unauthorized":
            badges.append(self._badge("No Autorizado", theme.WARN))
        else:
            badges.append(self._badge(state.upper(), theme.ERR))

        # Activo
        if is_active:
            badges.append(self._badge("ACTIVO", theme.ACCENT, is_bold=True))

        # Batería
        if battery is not None:
            badges.append(self._badge(f"Bat: {battery}%", theme.TEXT))

        # Acciones para este dispositivo
        action_buttons = []

        if not is_active and state == "device":
            action_buttons.append(
                ft.FilledButton(
                    "Usar como Activo",
                    icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                    height=30,
                    on_click=lambda _, s=serial: self._action_set_active(s),
                )
            )

        if not is_net and state == "device":
            action_buttons.append(
                ft.OutlinedButton(
                    "Pasar a Wi-Fi",
                    icon=ft.Icons.WIFI,
                    height=30,
                    on_click=lambda _, s=serial: self._action_convert_wifi(s),
                )
            )

        if is_net:
            action_buttons.append(
                ft.OutlinedButton(
                    "Ping",
                    icon=ft.Icons.NETWORK_CHECK,
                    height=30,
                    on_click=lambda _, s=serial: self._action_ping(s),
                )
            )

        action_buttons.append(
            ft.OutlinedButton(
                "Ficha",
                icon=ft.Icons.INFO_OUTLINE,
                height=30,
                on_click=lambda _, s=serial: self._load_specs(s),
            )
        )

        title = f"{friendly}" if friendly != serial else f"Dispositivo {serial}"
        subtitle = f"Serial / Endpoint: {serial}"
        if model and model != "—" and model not in title:
            subtitle += f" | Modelo: {model}"

        return ft.Container(
            padding=ft.Padding(12, 10, 12, 10),
            bgcolor=theme.SURFACE_ALT if is_active else theme.BG,
            border_radius=8,
            border=ft.Border.all(1, theme.ACCENT if is_active else theme.BORDER),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        wrap=True,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(
                                        ft.Icons.WIFI if is_net else ft.Icons.USB,
                                        color=theme.OK if is_active else theme.TEXT_MUTED,
                                        size=22,
                                    ),
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Text(title, weight=ft.FontWeight.BOLD, size=14),
                                            ft.Text(subtitle, size=11, color=theme.TEXT_MUTED),
                                        ],
                                    ),
                                ],
                                spacing=8,
                            ),
                            ft.Row(badges, spacing=6),
                        ],
                    ),
                    ft.Row(action_buttons, spacing=8, wrap=True),
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
            badges.append(self._badge("Conectado", theme.OK))
        else:
            badges.append(self._badge("Offline", theme.TEXT_MUTED))

        if is_active:
            badges.append(self._badge("ACTIVO", theme.ACCENT, is_bold=True))

        actions = [
            ft.FilledButton(
                "Conectar",
                icon=ft.Icons.PLAY_ARROW,
                height=30,
                on_click=lambda _, i=dev_id: self._action_activate_saved(i),
            ),
            ft.OutlinedButton(
                "Principal",
                icon=ft.Icons.STAR_BORDER,
                height=30,
                on_click=lambda _, i=dev_id: self._action_set_primary(i),
            ),
            ft.OutlinedButton(
                "Renombrar",
                icon=ft.Icons.EDIT_OUTLINED,
                height=30,
                on_click=lambda _, i=dev_id, n=friendly: self._open_rename_panel(i, n),
            ),
        ]

        if is_online:
            actions.append(
                ft.OutlinedButton(
                    "Desconectar",
                    icon=ft.Icons.LINK_OFF,
                    height=30,
                    on_click=lambda _, i=dev_id: self._action_disconnect(i),
                )
            )

        if ep and ":" in ep:
            actions.append(
                ft.OutlinedButton(
                    "Ping",
                    icon=ft.Icons.NETWORK_CHECK,
                    height=30,
                    on_click=lambda _, e=ep: self._action_ping(e),
                )
            )

        actions.append(
            ft.OutlinedButton(
                "Eliminar",
                icon=ft.Icons.DELETE_OUTLINE,
                height=30,
                on_click=lambda _, i=dev_id, n=friendly, s=(ep or usb or "—"): self._action_delete_saved(i, n, s),
            )
        )

        sub_info = f"Endpoint: {ep or '(sin IP)'} | USB: {usb or '(sin USB)'} | Modo: {mode}"
        if last_utc and last_utc != "—":
            sub_info += f" | Último uso: {last_utc[:19]}"

        return ft.Container(
            padding=ft.Padding(12, 10, 12, 10),
            bgcolor=theme.SURFACE_ALT if is_active else theme.BG,
            border_radius=8,
            border=ft.Border.all(1, theme.ACCENT if is_active else theme.BORDER),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        wrap=True,
                        controls=[
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.PHONE_ANDROID, color=theme.TEXT_MUTED, size=22),
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Text(friendly, weight=ft.FontWeight.BOLD, size=14),
                                            ft.Text(sub_info, size=11, color=theme.TEXT_MUTED),
                                        ],
                                    ),
                                ],
                                spacing=8,
                            ),
                            ft.Row(badges, spacing=6),
                        ],
                    ),
                    ft.Row(actions, spacing=8, wrap=True),
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

    def _action_activate_saved(self, device_id: str) -> None:
        self._set_busy(True, "Reconectando y activando dispositivo guardado...")

        def worker():
            ok, msg = self._device.set_active_by_saved_id(device_id)
            self._log(msg, theme.OK if ok else theme.WARN)
            self._set_busy(False, msg, is_error=not ok)
            if ok and self._on_device_changed:
                try:
                    self._on_device_changed(self._device.serial or "")
                except Exception:
                    pass
            self.refresh_devices()

        threading.Thread(target=worker, name="dev-activate-saved", daemon=True).start()

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
        ok, msg = self._device.rename_device(self._rename_id, new_name)
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
    # Helpers Visuales
    # -------------------------------------------------------------------------
    def _badge(self, text: str, color: str, is_bold: bool = False) -> ft.Container:
        return ft.Container(
            bgcolor=theme.SURFACE,
            border=ft.Border.all(1, color),
            border_radius=6,
            padding=ft.Padding(8, 3, 8, 3),
            content=ft.Text(text, size=11, color=color, weight=ft.FontWeight.BOLD if is_bold else ft.FontWeight.NORMAL),
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
        pass
