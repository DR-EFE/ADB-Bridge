"""Panel lateral izquierdo: selector de dispositivo, estado con alertas, botones de reconexión y Wi-Fi."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import flet as ft

from .. import theme
from ..services.device_service import ConnectionResult, DeviceService, DeviceStatus, RetryPolicy
from .status_card import StatusCard

logger = logging.getLogger("ui.sidebar")

POLL_SECONDS = 4.0


class Sidebar:
    """Sidebar con sondeo de estado y gestión de conexiones USB / Wi-Fi."""

    def __init__(
        self,
        device: DeviceService,
        on_status: Optional[Callable[[DeviceStatus], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._device = device
        self._on_status = on_status
        self._on_log = on_log
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._watch_handle = None

        self._card = StatusCard(on_convert_wifi=self._on_convert_wifi_click)

        # Selector de dispositivos detectados
        self._dev_dropdown = ft.Dropdown(
            label="Dispositivo activo",
            text_size=12,
            content_padding=8,
            options=[],
            on_select=self._on_device_selected,
        )

        self._reconnect_btn = ft.FilledButton(
            "Reconectar / Refrescar",
            icon=ft.Icons.REFRESH,
            on_click=self._on_reconnect_click,
            expand=True,
        )
        self._wireless_setup_btn = ft.OutlinedButton(
            "Configurar Wireless Debugging moderno",
            icon=ft.Icons.PHONELINK_SETUP,
            on_click=self._on_wireless_setup_click,
            expand=True,
        )
        self._wifi_search_btn = ft.OutlinedButton(
            "Buscar dispositivo WiFi",
            icon=ft.Icons.WIFI_FIND,
            on_click=self._on_wifi_search_click,
            expand=True,
        )
        self._watchdog_label = ft.Text("Pausar reconexión automática")
        self._watchdog_btn = ft.OutlinedButton(
            content=self._watchdog_label,
            icon=ft.Icons.PAUSE_CIRCLE_OUTLINE,
            on_click=self._on_watchdog_toggle,
            expand=True,
        )
        self._manual_endpoint_btn = ft.OutlinedButton(
            "Conectar endpoint manual",
            icon=ft.Icons.LINK,
            on_click=self._on_manual_endpoint_click,
            expand=True,
        )
        self._feedback = ft.Text("", size=12, color=theme.TEXT_MUTED)

        self.view = ft.Container(
            width=300,
            padding=16,
            bgcolor=theme.SURFACE,
            content=ft.Column(
                spacing=14,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.ANDROID, color=theme.OK, size=28),
                            ft.Text("Android Tools", size=20, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=10,
                    ),
                    self._dev_dropdown,
                    self._card.view,
                    ft.Row([self._reconnect_btn]),
                    ft.Row([self._wireless_setup_btn]),
                    ft.Row([self._wifi_search_btn]),
                    ft.Row([self._manual_endpoint_btn]),
                    ft.Row([self._watchdog_btn]),
                    self._feedback,
                ],
            ),
        )

    def set_message(self, text: str, color: str = theme.WARN) -> None:
        self._card.set_message(text, color)

    def start_polling(self) -> None:
        """Lanza el sondeo en segundo plano."""
        self._watch_handle = self._device.start_connection_watchdog(
            retry_policy=RetryPolicy(initial_interval=2.0, max_interval=30.0, timeout=120.0),
            on_result=self._on_watchdog_result,
        )
        self._set_watchdog_label("Pausar reconexión automática")
        threading.Thread(target=self._poll_loop, name="sidebar-poll", daemon=True).start()

    def stop_polling(self) -> None:
        self._stop.set()
        self._device.stop_connection_watchdog()
        self._set_watchdog_label("Activar reconexión automática")

    def refresh_now(self) -> DeviceStatus:
        """Consulta y actualiza el estado en pantalla."""
        status = self._device.get_status()
        self._card.update_status(status)

        # Actualizar opciones del dropdown
        options = []
        for d in status.available_devices:
            label = f"{d.serial} ({d.state})"
            options.append(ft.dropdown.Option(key=d.serial, text=label))

        self._dev_dropdown.options = options
        if status.serial and any(opt.key == status.serial for opt in options):
            self._dev_dropdown.value = status.serial
        elif options:
            self._dev_dropdown.value = options[0].key

        try:
            self._dev_dropdown.update()
        except Exception:
            pass

        if self._on_status:
            self._on_status(status)

        # Disparar auto-conversión a Wi-Fi si hay un USB en estado 'device'
        self._device.check_and_auto_convert_usb(on_result=self._on_auto_wifi_result)

        return status

    def _on_auto_wifi_result(self, ok: bool, msg: str) -> None:
        color = theme.OK if ok else theme.WARN
        self._log_msg(f"⚡ [Auto-WiFi] {msg}", color)
        self._feedback.value = msg
        self._feedback.color = color
        try:
            self.refresh_now()
        except Exception:
            pass
        try:
            self.view.update()
        except Exception:
            pass

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

    def _on_device_selected(self, e: ft.ControlEvent) -> None:
        new_serial = self._dev_dropdown.value
        if new_serial and new_serial != self._device.serial:
            self._device.serial = new_serial
            self._device.stop_connection_watchdog()
            self._watch_handle = self._device.start_connection_watchdog(on_result=self._on_watchdog_result)
            self._set_watchdog_label("Pausar reconexión automática")
            self._log_msg(f"Dispositivo activo cambiado a: {new_serial}", theme.ACCENT)
            threading.Thread(target=self.refresh_now, daemon=True).start()

    def _on_reconnect_click(self, _e: ft.ControlEvent) -> None:
        self._reconnect_btn.disabled = True
        self._feedback.value = "Consultando ADB / Reconectando..."
        self.view.update()
        threading.Thread(target=self._reconnect_worker, name="sidebar-reconnect", daemon=True).start()

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
                try:
                    self.refresh_now()
                except Exception:
                    pass
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
            if len(result.candidates) > 1 and not result.success:
                self._log_msg(f"[Buscar WiFi] {result.message}", theme.WARN)
                self._feedback.value = result.message
                self._feedback.color = theme.WARN
                self._wifi_search_btn.disabled = False
                try:
                    self.view.update()
                except Exception:
                    pass
                self._show_wireless_candidates(result)
                return
            ok = result.success
            message = result.message
            self._log_msg(f"[Buscar WiFi] {message}", theme.OK if ok else theme.WARN)
            self._feedback.value = message
            self._feedback.color = theme.OK if ok else theme.WARN
            try:
                self.refresh_now()
            except Exception:
                pass
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
        endpoint = ft.TextField(label="IP, DNS o endpoint Tailscale (host:puerto)", autofocus=True)

        def close(_e=None) -> None:
            try:
                page.pop_dialog()
            except Exception:
                pass

        def connect(_e) -> None:
            value = (endpoint.value or "").strip()
            if not value or ":" not in value:
                endpoint.error_text = "Escribe un host y puerto, por ejemplo 100.64.0.5:37123"
                endpoint.update()
                return
            close()
            self._manual_endpoint_btn.disabled = True
            self._feedback.value = f"Conectando a {value}..."
            self.view.update()

            def worker() -> None:
                result = self._device.discover_and_reconnect_wireless(endpoint=value)
                self._feedback.value = result.message
                self._feedback.color = theme.OK if result.success else theme.ERR
                self._log_msg(f"[Endpoint manual] {result.message}", self._feedback.color)
                self._manual_endpoint_btn.disabled = False
                try:
                    self.refresh_now()
                    self.view.update()
                except Exception:
                    pass

            threading.Thread(target=worker, name="manual-endpoint", daemon=True).start()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Conectar endpoint privado o VPN"),
            content=ft.Column([ft.Text("Usa la IP/DNS y puerto de Wireless Debugging. No abre puertos ni configura la VPN."), endpoint], tight=True, width=460),
            actions=[ft.TextButton("Cancelar", on_click=close), ft.FilledButton("Conectar", on_click=connect)],
        )
        page.show_dialog(dialog)

    def _show_wireless_candidates(self, result: ConnectionResult) -> None:
        page = getattr(self.view, "page", None)
        if page is None:
            return
        options = [ft.dropdown.Option(key=item.get("endpoint", ""), text=f"{item.get('endpoint', '')}  {item.get('service_name', '')}") for item in result.candidates if item.get("endpoint")]
        selector = ft.Dropdown(label="Dispositivo / endpoint", options=options, value=options[0].key if options else None)

        def close(_e=None) -> None:
            try:
                page.pop_dialog()
            except Exception:
                pass

        def connect(_e) -> None:
            chosen = selector.value
            if not chosen:
                return
            close()
            self._wifi_search_btn.disabled = True
            self._feedback.value = f"Conectando a {chosen}..."
            self.view.update()

            def worker() -> None:
                selected = self._device.discover_and_reconnect_wireless(endpoint=chosen)
                self._feedback.value = selected.message
                self._feedback.color = theme.OK if selected.success else theme.ERR
                self._log_msg(f"[Buscar WiFi] {selected.message}", self._feedback.color)
                self._wifi_search_btn.disabled = False
                try:
                    self.refresh_now()
                    self.view.update()
                except Exception:
                    pass

            threading.Thread(target=worker, name="wireless-candidate", daemon=True).start()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Dispositivos Wireless Debugging encontrados"),
            content=ft.Column([ft.Text("Selecciona el teléfono que quieres conectar."), selector], tight=True, width=460),
            actions=[ft.TextButton("Cancelar", on_click=close), ft.FilledButton("Conectar", on_click=connect)],
        )
        page.show_dialog(dialog)

    def _on_watchdog_result(self, result: ConnectionResult) -> None:
        if result.success:
            self._log_msg(f"[Reconexión automática] {result.message}", theme.OK)
            self._feedback.value = result.message
            self._feedback.color = theme.OK
            try:
                self._feedback.update()
            except Exception:
                pass
        elif result.message:
            logger.debug("Reconexión automática: %s", result.message)

    def _on_watchdog_toggle(self, _e: ft.ControlEvent) -> None:
        if self._watch_handle and self._watch_handle.is_alive():
            self._device.stop_connection_watchdog()
            self._set_watchdog_label("Activar reconexión automática")
            self._feedback.value = "Reconexión automática pausada."
            self._feedback.color = theme.WARN
        else:
            self._watch_handle = self._device.start_connection_watchdog(on_result=self._on_watchdog_result)
            self._set_watchdog_label("Pausar reconexión automática")
            self._feedback.value = "Reconexión automática activada."
            self._feedback.color = theme.OK
        try:
            self.view.update()
        except Exception:
            pass

    def _set_watchdog_label(self, text: str) -> None:
        self._watchdog_label.value = text

    def _reconnect_worker(self) -> None:
        with self._busy:
            msg = self._device.reconnect()
            self._log_msg(f"Reconexión: {msg}", theme.TEXT)
            try:
                self.refresh_now()
            except Exception as exc:
                logger.debug("Error al refrescar tras reconectar: %s", exc)
        self._feedback.value = msg
        self._reconnect_btn.disabled = False
        try:
            self.view.update()
        except Exception:
            pass

    def _on_convert_wifi_click(self) -> None:
        self._feedback.value = "Convirtiendo a Wi-Fi (configurando TCP/IP 5555)..."
        self._feedback.color = theme.WARN
        self.view.update()
        threading.Thread(target=self._wifi_convert_worker, name="wifi-convert", daemon=True).start()

    def _wifi_convert_worker(self) -> None:
        with self._busy:
            ok, msg = self._device.convert_usb_to_wifi()
            color = theme.OK if ok else theme.ERR
            self._log_msg(msg, color)
            self._feedback.value = msg
            self._feedback.color = color
            try:
                self.refresh_now()
            except Exception:
                pass
        try:
            self.view.update()
        except Exception:
            pass

    def _log_msg(self, text: str, color: str = theme.TEXT) -> None:
        if self._on_log:
            self._on_log(text, color)
        else:
            logger.info(text)
