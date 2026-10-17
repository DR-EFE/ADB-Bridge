"""Panel lateral izquierdo: administrador visual de dispositivos, lista de tarjetas, selección segura y acciones."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from .. import theme
from ..services.device_service import ConnectionResult, DeviceRecord, DeviceService, DeviceStatus

logger = logging.getLogger("ui.sidebar")

POLL_SECONDS = 4.0


class Sidebar:
    """Sidebar con administrador visual de dispositivos, selección segura y acciones completas."""

    def __init__(
        self,
        device: DeviceService,
        on_status: Optional[Callable[[DeviceStatus], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_collapse: Optional[Callable[[], None]] = None,
    ) -> None:
        self._device = device
        self._on_status = on_status
        self._on_log = on_log
        self._on_collapse = on_collapse
        self._stop = threading.Event()
        self._busy = threading.Lock()

        self._selection_buttons_row = ft.Row(wrap=True, spacing=6)

        # Banner de selección obligatoria si hay varios dispositivos listos
        self._mandatory_selection_banner = ft.Container(
            visible=False,
            bgcolor="#3d2800",
            border=ft.Border.all(1.5, theme.WARN),
            border_radius=8,
            padding=10,
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.WARNING_ROUNDED, color=theme.WARN, size=20),
                            ft.Text("SELECCIÓN OBLIGATORIA", weight=ft.FontWeight.BOLD, color=theme.WARN, size=12),
                        ],
                        spacing=6,
                    ),
                    ft.Text(
                        "Hay varios teléfonos listos. Selecciona cuál usar para evitar operaciones accidentales:",
                        size=11,
                        color=theme.TEXT,
                    ),
                    self._selection_buttons_row,
                ],
            ),
        )

        # Contenedor dinámico para la lista de tarjetas de dispositivos
        self._device_cards_col = ft.Column(spacing=10)

        # Botones de acción global
        self._reconnect_btn = ft.FilledButton(
            "Reconectar / Refrescar",
            icon=ft.Icons.REFRESH,
            on_click=self._on_reconnect_click,
            expand=True,
        )
        self._wireless_setup_btn = ft.OutlinedButton(
            "Configurar Wireless Debugging",
            icon=ft.Icons.PHONELINK_SETUP,
            on_click=self._on_wireless_setup_click,
            expand=True,
        )
        self._wifi_search_btn = ft.OutlinedButton(
            "Buscar mDNS / WiFi",
            icon=ft.Icons.WIFI_FIND,
            on_click=self._on_wifi_search_click,
            expand=True,
        )
        self._manual_endpoint_btn = ft.OutlinedButton(
            "Conectar endpoint manual / VPN",
            icon=ft.Icons.LINK,
            on_click=self._on_manual_endpoint_click,
            expand=True,
        )
        self._feedback = ft.Text("", size=11, color=theme.TEXT_MUTED)

        self.view = ft.Container(
            width=320,
            padding=14,
            bgcolor=theme.SURFACE,
            content=ft.Column(
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.ANDROID, color=theme.OK, size=28),
                                    ft.Column(
                                        spacing=1,
                                        controls=[
                                            ft.Text("Android Tools", size=18, weight=ft.FontWeight.BOLD),
                                            ft.Text("Administrador de Dispositivos", size=11, color=theme.TEXT_MUTED),
                                        ],
                                    ),
                                ],
                                spacing=10,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CHEVRON_LEFT,
                                tooltip="Ocultar panel lateral (Dispositivos)",
                                on_click=lambda _: self._on_collapse() if self._on_collapse else None,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self._mandatory_selection_banner,
                    ft.Row(
                        [
                            ft.Text("DISPOSITIVOS", size=11, weight=ft.FontWeight.BOLD, color=theme.TEXT_MUTED),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self._device_cards_col,
                    ft.Divider(height=1, color=theme.BORDER),
                    ft.Row([self._reconnect_btn]),
                    ft.Row([self._wireless_setup_btn]),
                    ft.Row([self._wifi_search_btn]),
                    ft.Row([self._manual_endpoint_btn]),
                    self._feedback,
                ],
            ),
        )

    def set_message(self, text: str, color: str = theme.WARN) -> None:
        """Establece un mensaje de estado o alerta en el pie del panel lateral."""
        self._feedback.value = text
        self._feedback.color = color
        try:
            self._feedback.update()
        except Exception:
            pass

    def start_polling(self) -> None:
        """Lanza el sondeo en segundo plano."""
        threading.Thread(target=self._poll_loop, name="sidebar-poll", daemon=True).start()

    def stop_polling(self) -> None:
        self._stop.set()

    def refresh_now(self) -> DeviceStatus:
        """Consulta y actualiza el estado y las tarjetas en pantalla."""
        status = self._device.get_status()
        mgmt_data = self._device.get_management_data()

        # Actualizar banner de selección obligatoria
        if status.requires_selection:
            self._mandatory_selection_banner.visible = True
            btn_controls = []
            for d in status.available_devices:
                if d.state == "device":
                    btn_controls.append(
                        ft.FilledButton(
                            f"{d.friendly_name or d.serial}",
                            height=28,
                            style=ft.ButtonStyle(padding=ft.Padding(8, 2, 8, 2)),
                            on_click=lambda _e, s=d.serial: self._on_select_device_serial(s),
                        )
                    )
            self._selection_buttons_row.controls = btn_controls
        else:
            self._mandatory_selection_banner.visible = False

        # Actualizar tarjetas visuales de dispositivos
        cards_controls = []
        for card_data in mgmt_data.get("cards", []):
            cards_controls.append(self._build_device_card(card_data, status))

        if not cards_controls:
            cards_controls.append(
                ft.Container(
                    padding=16,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=8,
                    content=ft.Text(
                        "No hay dispositivos guardados ni detectados.\nConecta un teléfono por USB o WiFi.",
                        size=11,
                        color=theme.TEXT_MUTED,
                        text_align=ft.TextAlign.CENTER,
                    ),
                )
            )

        self._device_cards_col.controls = cards_controls

        try:
            self.view.update()
        except Exception:
            pass

        if self._on_status:
            self._on_status(status)

        # Disparar auto-conversión a Wi-Fi si hay un USB en estado 'device'
        self._device.check_and_auto_convert_usb(on_result=self._on_auto_wifi_result)

        return status

    def _build_device_card(self, card: Dict[str, Any], status: DeviceStatus) -> ft.Container:
        """Construye una tarjeta visual completa para un dispositivo."""
        is_active = card.get("is_active", False)
        is_primary = card.get("is_primary", False)
        state_str = card.get("state", "no detectado")

        if state_str == "conectado":
            state_color = theme.OK
            state_icon = ft.Icons.CHECK_CIRCLE
        elif state_str == "no autorizado":
            state_color = theme.ERR
            state_icon = ft.Icons.ERROR_OUTLINE
        elif state_str == "offline":
            state_color = theme.WARN
            state_icon = ft.Icons.PAUSE_CIRCLE_OUTLINE
        else:
            state_color = theme.TEXT_MUTED
            state_icon = ft.Icons.CANCEL_OUTLINED

        bat_val = card.get("battery")
        bat_text = f"{bat_val}%" if bat_val is not None else "—"
        bat_color = theme.WARN if (bat_val is not None and bat_val <= 20) else theme.TEXT_MUTED

        top_badges = []
        if is_primary:
            top_badges.append(
                ft.Container(
                    padding=ft.Padding(4, 2, 4, 2),
                    bgcolor="#332b00",
                    border=ft.Border.all(1, theme.ACCENT),
                    border_radius=4,
                    content=ft.Row(
                        spacing=3,
                        controls=[
                            ft.Icon(ft.Icons.STAR, size=12, color=theme.ACCENT),
                            ft.Text("Principal", size=10, weight=ft.FontWeight.BOLD, color=theme.ACCENT),
                        ],
                    ),
                )
            )
        if is_active:
            top_badges.append(
                ft.Container(
                    padding=ft.Padding(4, 2, 4, 2),
                    bgcolor="#0d331a",
                    border=ft.Border.all(1, theme.OK),
                    border_radius=4,
                    content=ft.Text("Activo", size=10, weight=ft.FontWeight.BOLD, color=theme.OK),
                )
            )

        action_buttons: List[ft.Control] = []
        dev_id = card.get("id")

        if state_str == "conectado":
            if not is_active:
                action_buttons.append(
                    ft.TextButton(
                        "Seleccionar",
                        icon=ft.Icons.CHECK,
                        style=ft.ButtonStyle(padding=ft.Padding(6, 2, 6, 2)),
                        on_click=lambda _e, s=card["current_endpoint"]: self._on_select_device_serial(s),
                    )
                )
            if dev_id:
                action_buttons.append(
                    ft.TextButton(
                        "Desconectar",
                        icon=ft.Icons.LINK_OFF,
                        style=ft.ButtonStyle(padding=ft.Padding(6, 2, 6, 2)),
                        on_click=lambda _e, did=dev_id: self._handle_disconnect(did),
                    )
                )
        else:
            if dev_id:
                action_buttons.append(
                    ft.FilledButton(
                        "Conectar",
                        icon=ft.Icons.LINK,
                        height=26,
                        style=ft.ButtonStyle(padding=ft.Padding(8, 2, 8, 2)),
                        on_click=lambda _e, did=dev_id: self._handle_connect(did),
                    )
                )
            elif card.get("current_endpoint"):
                action_buttons.append(
                    ft.FilledButton(
                        "Seleccionar",
                        icon=ft.Icons.CHECK,
                        height=26,
                        style=ft.ButtonStyle(padding=ft.Padding(8, 2, 8, 2)),
                        on_click=lambda _e, s=card["current_endpoint"]: self._on_select_device_serial(s),
                    )
                )

        if dev_id:
            if not is_primary:
                action_buttons.append(
                    ft.IconButton(
                        ft.Icons.STAR_BORDER,
                        tooltip="Establecer como principal",
                        icon_size=16,
                        on_click=lambda _e, did=dev_id: self._handle_set_primary(did),
                    )
                )
            action_buttons.append(
                ft.IconButton(
                    ft.Icons.EDIT_OUTLINED,
                    tooltip="Renombrar",
                    icon_size=16,
                    on_click=lambda _e, did=dev_id, cur_name=card["friendly_name"]: self._show_rename_dialog(did, cur_name),
                )
            )
            action_buttons.append(
                ft.IconButton(
                    ft.Icons.DELETE_OUTLINE,
                    tooltip="Eliminar",
                    icon_color=theme.ERR,
                    icon_size=16,
                    on_click=lambda _e, did=dev_id, name=card["friendly_name"], s=card["current_endpoint"]: self._show_remove_dialog(did, name, s),
                )
            )

        associated = card.get("associated_endpoints", [])
        endpoints_str = " | ".join(associated) if associated else card.get("current_endpoint", "—")

        border_color = theme.OK if is_active else (theme.ACCENT if is_primary else theme.BORDER)
        border_width = 1.5 if (is_active or is_primary) else 1.0

        return ft.Container(
            padding=10,
            border=ft.Border.all(border_width, border_color),
            border_radius=8,
            bgcolor="#1b2029" if is_active else theme.CARD_BG,
            content=ft.Column(
                spacing=5,
                controls=[
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.PHONE_ANDROID, size=18, color=state_color),
                                    ft.Text(card.get("friendly_name", "Android"), size=13, weight=ft.FontWeight.BOLD, expand=True),
                                ],
                                expand=True,
                                spacing=6,
                            ),
                            ft.Row(top_badges, spacing=4),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        [
                            ft.Text(f"Modelo: {card.get('model', '—')}", size=11, color=theme.TEXT_MUTED),
                            ft.Text(f"Android {card.get('android_version', '—')}", size=11, color=theme.TEXT_MUTED),
                            ft.Text(f"Bat: {bat_text}", size=11, color=bat_color),
                        ],
                        spacing=8,
                    ),
                    ft.Row(
                        [
                            ft.Container(
                                padding=ft.Padding(4, 1, 4, 1),
                                border_radius=4,
                                bgcolor="#232936",
                                content=ft.Text(card.get("mode", "tcpip"), size=10, color=theme.TEXT_MUTED),
                            ),
                            ft.Row(
                                [
                                    ft.Icon(state_icon, size=13, color=state_color),
                                    ft.Text(state_str.capitalize(), size=11, color=state_color, weight=ft.FontWeight.W_500),
                                ],
                                spacing=4,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Text(f"Endpoint: {endpoints_str}", size=10, color=theme.TEXT_MUTED, selectable=True),
                    ft.Text(f"Última conexión: {card.get('last_connected', '—')}", size=10, color=theme.TEXT_MUTED),
                    ft.Row(action_buttons, alignment=ft.MainAxisAlignment.END, spacing=2),
                ],
            ),
        )

    # -------------------------------------------------------------------------
    # Acciones de Dispositivo
    # -------------------------------------------------------------------------
    def _on_select_device_serial(self, serial: str) -> None:
        """Selecciona un serial explícitamente."""
        ok, msg = self._device.set_active_by_serial(serial)
        self._log_msg(msg, theme.OK if ok else theme.WARN)
        self._feedback.value = msg
        self._feedback.color = theme.OK if ok else theme.WARN
        threading.Thread(target=self.refresh_now, daemon=True).start()

    def _handle_connect(self, device_id: str) -> None:
        self._feedback.value = "Conectando mediante reconexión inteligente..."
        self._feedback.color = theme.WARN
        self.view.update()

        def worker():
            res = self._device.connect_device(device_id)
            color = theme.OK if res.success else theme.ERR
            msg = res.message or res.error or "Resultado desconocido"
            self._log_msg(f"[Conectar] {msg}", color)
            self._feedback.value = msg
            self._feedback.color = color
            self.refresh_now()

        threading.Thread(target=worker, name="connect-worker", daemon=True).start()

    def _handle_disconnect(self, device_id: str) -> None:
        self._feedback.value = "Desconectando dispositivo..."
        self._feedback.color = theme.WARN
        self.view.update()

        def worker():
            res = self._device.disconnect_device(device_id)
            color = theme.OK if res.success else theme.ERR
            msg = res.message or res.error or "Desconectado."
            self._log_msg(f"[Desconectar] {msg}", color)
            self._feedback.value = msg
            self._feedback.color = color
            self.refresh_now()

        threading.Thread(target=worker, name="disconnect-worker", daemon=True).start()

    def _handle_set_primary(self, device_id: str) -> None:
        self._feedback.value = "Estableciendo como dispositivo principal..."
        self._feedback.color = theme.ACCENT
        self.view.update()

        def worker():
            res = self._device.set_primary_device(device_id)
            color = theme.OK if res.success else theme.WARN
            msg = res.message or res.error or "Dispositivo principal actualizado."
            self._log_msg(f"[Principal] {msg}", color)
            self._feedback.value = msg
            self._feedback.color = color
            self.refresh_now()

        threading.Thread(target=worker, name="primary-worker", daemon=True).start()

    # -------------------------------------------------------------------------
    # Diálogos: Renombrar y Eliminar
    # -------------------------------------------------------------------------
    def _show_rename_dialog(self, device_id: str, current_name: str) -> None:
        page = getattr(self.view, "page", None)
        if not page:
            return

        name_field = ft.TextField(
            label="Nombre del dispositivo",
            value=current_name,
            autofocus=True,
            dense=True,
        )

        def close_dialog(_e=None):
            try:
                page.pop_dialog()
            except Exception:
                pass

        def save_rename(_e):
            val = (name_field.value or "").strip()
            if not val:
                name_field.error_text = "El nombre no puede estar vacío."
                name_field.update()
                return
            close_dialog()
            res = self._device.rename_device(device_id, val)
            color = theme.OK if res.success else theme.ERR
            msg = res.message or res.error or ""
            self._log_msg(f"[Renombrar] {msg}", color)
            self._feedback.value = msg
            self._feedback.color = color
            self.refresh_now()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Renombrar Dispositivo"),
            content=ft.Column([name_field], tight=True, width=320),
            actions=[
                ft.TextButton("Cancelar", on_click=close_dialog),
                ft.FilledButton("Guardar", on_click=save_rename),
            ],
        )
        page.show_dialog(dialog)

    def _show_remove_dialog(self, device_id: str, friendly_name: str, serial: str) -> None:
        page = getattr(self.view, "page", None)
        if not page:
            return

        def close_dialog(_e=None):
            try:
                page.pop_dialog()
            except Exception:
                pass

        def confirm_remove(_e):
            close_dialog()
            self._feedback.value = f"Eliminando {friendly_name}..."
            self._feedback.color = theme.WARN
            self.view.update()

            def worker():
                res = self._device.remove_device(device_id)
                color = theme.OK if res.success else theme.ERR
                msg = res.message or res.error or "Eliminado."
                self._log_msg(f"[Eliminar] {msg}", color)
                self._feedback.value = msg
                self._feedback.color = color
                self.refresh_now()

            threading.Thread(target=worker, name="remove-worker", daemon=True).start()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Confirmar eliminación"),
            content=ft.Column(
                [
                    ft.Text(f"¿Estás seguro de eliminar el dispositivo '{friendly_name}'?", weight=ft.FontWeight.BOLD),
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

    # -------------------------------------------------------------------------
    # Conexión mDNS / Wireless Debugging / Endpoint Manual
    # -------------------------------------------------------------------------
    def _on_wireless_setup_click(self, _e: ft.ControlEvent) -> None:
        page = getattr(self.view, "page", None)
        if page is None:
            self._feedback.value = "La ventana todavía no está lista."
            return

        host = ft.TextField(label="IP del teléfono", autofocus=True)
        port = ft.TextField(label="Puerto de emparejamiento", value="", keyboard_type=ft.KeyboardType.NUMBER)
        code = ft.TextField(label="Código de 6 dígitos", password=True, can_reveal_password=False)
        name = ft.TextField(label="Nombre (opcional)", value="Android Wireless")

        def close_dialog(_e=None) -> None:
            try:
                page.pop_dialog()
            except Exception:
                pass

        def start_pair(_e) -> None:
            try:
                parsed_port = int((port.value or "").strip())
            except ValueError:
                port.error_text = "Escribe el puerto mostrado en Wireless Debugging"
                port.update()
                return
            if not host.value or not code.value:
                self._feedback.value = "Completa IP, puerto y código de emparejamiento."
                self._feedback.color = theme.WARN
                self._feedback.update()
                return
            close_dialog()
            self._wireless_setup_btn.disabled = True
            self._feedback.value = "Emparejando Wireless Debugging..."
            self._feedback.color = theme.WARN
            self.view.update()

            def worker() -> None:
                ok, message = self._device.pair_wireless(
                    host.value.strip(),
                    parsed_port,
                    code.value.strip(),
                    (name.value or "").strip(),
                    usb_serial=self._device.serial,
                )
                color = theme.OK if ok else theme.ERR
                self._log_msg(f"[Wireless Debugging] {message}", color)
                self._feedback.value = message
                self._feedback.color = color
                self.refresh_now()
                self._wireless_setup_btn.disabled = False
                try:
                    self.view.update()
                except Exception:
                    pass

            threading.Thread(target=worker, name="wireless-pair", daemon=True).start()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Configurar Wireless Debugging moderno"),
            content=ft.Column(
                [
                    ft.Text("En el teléfono abre Ajustes → Wireless Debugging → Emparejar con código."),
                    host,
                    port,
                    code,
                    name,
                ],
                tight=True,
                width=420,
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=close_dialog),
                ft.FilledButton("Emparejar", on_click=start_pair),
            ],
        )
        page.show_dialog(dialog)

    def _on_wifi_search_click(self, _e: ft.ControlEvent) -> None:
        self._wifi_search_btn.disabled = True
        self._feedback.value = "Buscando servicios Wireless Debugging por mDNS..."
        self._feedback.color = theme.WARN
        self.view.update()

        def worker() -> None:
            result = self._device.discover_and_reconnect_wireless()
            msg = result.message if isinstance(result, ConnectionResult) else str(result)
            ok = result.success if isinstance(result, ConnectionResult) else msg.startswith("Conectado por Wireless Debugging")
            self._log_msg(f"[Buscar WiFi] {msg}", theme.OK if ok else theme.WARN)
            self._feedback.value = msg
            self._feedback.color = theme.OK if ok else theme.WARN
            self.refresh_now()
            self._wifi_search_btn.disabled = False
            try:
                self.view.update()
            except Exception:
                pass

        threading.Thread(target=worker, name="wireless-discovery", daemon=True).start()

    def _on_manual_endpoint_click(self, _e: ft.ControlEvent) -> None:
        page = getattr(self.view, "page", None)
        if page is None:
            return
        endpoint_field = ft.TextField(label="IP o IP:Puerto (ej. 192.168.1.185:5555)", autofocus=True)
        name_field = ft.TextField(label="Nombre personalizado (opcional)", value="")

        def close_dialog(_e=None) -> None:
            try:
                page.pop_dialog()
            except Exception:
                pass

        def start_connect(_e) -> None:
            val = (endpoint_field.value or "").strip()
            if not val:
                endpoint_field.error_text = "Escribe una dirección válida"
                endpoint_field.update()
                return
            close_dialog()
            self._manual_endpoint_btn.disabled = True
            self._feedback.value = f"Conectando a {val}..."
            self._feedback.color = theme.WARN
            self.view.update()

            def worker() -> None:
                ok, msg = self._device.connect_endpoint(val, friendly_name=(name_field.value or "").strip())
                color = theme.OK if ok else theme.ERR
                self._log_msg(f"[Endpoint Manual] {msg}", color)
                self._feedback.value = msg
                self._feedback.color = color
                self.refresh_now()
                self._manual_endpoint_btn.disabled = False
                try:
                    self.view.update()
                except Exception:
                    pass

            threading.Thread(target=worker, name="manual-endpoint", daemon=True).start()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Conectar endpoint manual / privado"),
            content=ft.Column(
                [
                    ft.Text("Conecta directamente por IP o túnel VPN/Tailscale.", size=12, color=theme.TEXT_MUTED),
                    endpoint_field,
                    name_field,
                ],
                tight=True,
                width=380,
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=close_dialog),
                ft.FilledButton("Conectar", on_click=start_connect),
            ],
        )
        page.show_dialog(dialog)

    def _reconnect_click_worker(self) -> None:
        with self._busy:
            msg = self._device.reconnect()
            self._log_msg(f"Reconexión: {msg}", theme.TEXT)
            self.refresh_now()
        self._feedback.value = msg
        self._reconnect_btn.disabled = False
        try:
            self.view.update()
        except Exception:
            pass

    def _on_reconnect_click(self, _e: ft.ControlEvent) -> None:
        self._reconnect_btn.disabled = True
        self._feedback.value = "Consultando ADB / Reconectando..."
        self.view.update()
        threading.Thread(target=self._reconnect_click_worker, name="sidebar-reconnect", daemon=True).start()

    def _on_auto_wifi_result(self, ok: bool, msg: str) -> None:
        color = theme.OK if ok else theme.WARN
        self._log_msg(f"[Auto-WiFi] {msg}", color)
        self._feedback.value = msg
        self._feedback.color = color
        self.refresh_now()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if self._busy.acquire(blocking=False):
                try:
                    self.refresh_now()
                except Exception as exc:
                    logger.debug("Fallo en sondeo: %s", exc)
                finally:
                    self._busy.release()
            self._stop.wait(POLL_SECONDS)

    def _log_msg(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)
