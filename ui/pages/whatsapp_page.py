"""Pestaña «WhatsApp Extractor»: exporta los N chats más recientes en un hilo secundario."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import flet as ft

from ui_automation.device import DeviceManager
from ui_automation.whatsapp import (
    WhatsAppClient,
    clear_failed_install_cache,
    ensure_receiver_ready,
    install_receiver,
    uninstall_receiver,
)
from ui_automation.whatsapp.processor import ChatFilter, export_processed_chat, process_chat_file

from .. import theme
from ..components.log_view import LogView, UiLogHandler, level_color
from ..services.device_service import DeviceService
from ..services.job_queue_service import JobStatus, JobType
from ..tasks import BackgroundTask

logger = logging.getLogger("ui.whatsapp")

LOG_PREFIXES = ("ui_automation.whatsapp", "ui.whatsapp")
# chats.py loguea: "[3/10] Iniciando exportación de 'X'..."
_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\] Iniciando exportación")


def select_discovered_chats(chat_names: List[str], selection: str) -> List[str]:
    """Convierte ``1,3-5`` en nombres de chats de la exploracion vigente."""
    value = (selection or "").strip()
    if not value:
        return []
    if not chat_names:
        raise ValueError("Primero usa 'Explorar chats' para obtener la lista numerada.")

    indexes: List[int] = []
    for token in re.split(r"[,;\s]+", value):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", token)
        if not match:
            raise ValueError(f"Selecci\u00f3n inv\u00e1lida: '{token}'. Usa por ejemplo 1,3-5.")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end:
            raise ValueError(f"Rango inv\u00e1lido: '{token}'.")
        if start < 1 or end > len(chat_names):
            raise ValueError(f"'{token}' est\u00e1 fuera de la lista (1-{len(chat_names)}).")
        indexes.extend(range(start, end + 1))
    return [chat_names[index - 1] for index in dict.fromkeys(indexes)]


def split_chat_selection(value: str) -> tuple[List[str], str]:
    """Separa nombres directos de indices/rangos escritos en el mismo campo."""
    names: List[str] = []
    number_tokens: List[str] = []
    for token in re.split(r"[,;\n]+", value or ""):
        token = token.strip()
        if not token:
            continue
        if re.fullmatch(r"\d+(?:\s*-\s*\d+)?", token):
            number_tokens.append(token)
        else:
            names.append(token)
    return names, ",".join(number_tokens)


class WhatsAppPage:
    """Contenido de la pestaña. El control a insertar es ``.view``."""

    def __init__(self, device: DeviceService, job_queue: Optional[Any] = None) -> None:
        self._device = device
        self._job_queue = job_queue
        self._task: Optional[BackgroundTask] = None
        self._receiver_task: Optional[BackgroundTask] = None
        self._log_handler: Optional[UiLogHandler] = None
        self._results: List[Dict[str, Any]] = []
        self._current_chat_idx: int = 0
        self._total_chats: int = 0
        self._last_serial: Optional[str] = None
        self._last_out_dir: str = "./exports"
        self._processed: List[Dict[str, Any]] = []
        self._discovered_chat_names: List[str] = []
        self._discovery_result: Dict[str, Any] = {}
        self._discovery_serial: Optional[str] = None

        self._count = ft.TextField(label="Cantidad de chats", value="10", width=170,
                                   keyboard_type=ft.KeyboardType.NUMBER)
        self._chat_selection = ft.TextField(
            label="Chats específicos (separados por coma)",
            hint_text="Familia, +52 221 957 0759",
            width=330,
            tooltip="Si se llena, se exportan estos chats y se ignora la cantidad.",
        )
        self._chat_selection.label = "Chats por nombre o n\u00famero"
        self._chat_selection.hint_text = "Miriam, Familia o 1,3-5"
        self._chat_selection.tooltip = "Acepta nombres y los n\u00fameros/rangos de Explorar chats."
        self._chat_numbers = ft.TextField(
            label="N\u00fameros o rangos",
            hint_text="1,3-5,8",
            width=190,
            tooltip="Usa los n\u00fameros generados por Explorar chats.",
        )
        self._scan_scrolls = ft.TextField(
            label="Scrolls de exploraci\u00f3n",
            value="3",
            width=170,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._discover_btn = ft.OutlinedButton(
            "Explorar chats", icon=ft.Icons.PHOTO_CAMERA, on_click=self._on_discover_chats
        )
        self._media = ft.Switch(label="Incluir multimedia (.zip)", value=False)
        self._auto_process = ft.Switch(label="Auto-procesar chats", value=False)
        self._my_name = ft.TextField(label="Mi nombre en este grupo (opcional)", width=250)
        self._out_dir = ft.TextField(label="Carpeta de destino", value="./exports", width=260)
        self._open_output_btn = ft.OutlinedButton(
            "Abrir resultados",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda _e: self._open_path((self._out_dir.value or "./exports").strip()),
        )
        self._export_btn = ft.FilledButton("Exportar", icon=ft.Icons.DOWNLOAD, on_click=self._on_export)
        self._process_folder_btn = ft.OutlinedButton("Procesar carpeta existente", icon=ft.Icons.FOLDER_OPEN, on_click=self._on_process_folder)
        self._cancel_btn = ft.OutlinedButton("Cancelar", icon=ft.Icons.CANCEL, disabled=True, on_click=self._on_cancel)
        self._retry_downloads_btn = ft.OutlinedButton(
            "Reintentar descargas", icon=ft.Icons.REPLAY, disabled=True, on_click=self._on_retry_downloads
        )
        self._check_receiver_btn = ft.OutlinedButton(
            "Verificar auxiliar", icon=ft.Icons.FACT_CHECK_OUTLINED, on_click=self._on_check_receiver
        )
        self._install_receiver_btn = ft.OutlinedButton(
            "Instalar auxiliar", icon=ft.Icons.INSTALL_MOBILE, on_click=self._on_install_receiver
        )
        self._remove_receiver_btn = ft.OutlinedButton(
            "Quitar auxiliar", icon=ft.Icons.DELETE_OUTLINE, on_click=self._on_remove_receiver
        )
        self._receiver_status = ft.Text("Auxiliar: estado no verificado (clic en Verificar auxiliar)", size=11, color=theme.TEXT_MUTED)
        self._screen_lock_status = ft.Text("Pantalla: Comprobando...", size=11, color=theme.TEXT_MUTED)
        self._progress = ft.ProgressBar(value=0, bar_height=8, border_radius=4, color=theme.ACCENT, bgcolor=theme.BORDER)
        self._progress_text = ft.Text("Listo.", size=12, color=theme.TEXT_MUTED)
        self._search = ft.TextField(label="Buscar en mensajes procesados", width=300, on_change=self._refresh_processed_cards)
        self._filter_sender = ft.TextField(label="Filtrar remitente", width=210, on_change=self._refresh_processed_cards)
        self._only_media = ft.Switch(label="Solo con multimedia", value=False, on_change=self._refresh_processed_cards)
        self._processed_cards = ft.Column(spacing=8)
        self._discovery_gallery = ft.Row(spacing=12, scroll=ft.ScrollMode.AUTO)
        self._discovery_names = ft.Text("", selectable=True, color=theme.TEXT_MUTED)
        self._discovery_section = ft.Column(
            visible=False,
            spacing=8,
            controls=[
                ft.Row(
                    [
                        ft.Text("Exploraci\u00f3n de chats", size=14, weight=ft.FontWeight.W_600),
                        ft.TextButton("Abrir carpeta", on_click=self._open_discovery_folder),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                self._discovery_names,
                self._discovery_gallery,
            ],
        )
        self._log = LogView(height=300)

        self.view = ft.Column(
            expand=True,
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                theme.card(
                    ft.Row(
                        wrap=True, spacing=24, run_spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._count, self._chat_selection, self._chat_numbers, self._media, self._auto_process,
                            self._my_name, self._out_dir, self._open_output_btn,
                            self._scan_scrolls, self._discover_btn, self._export_btn,
                            self._process_folder_btn, self._cancel_btn,
                            self._retry_downloads_btn,
                            self._check_receiver_btn,
                            self._install_receiver_btn, self._remove_receiver_btn,
                            self._receiver_status,
                            self._screen_lock_status,
                        ],
                    )
                ),
                theme.card(ft.Column([self._progress, self._progress_text], spacing=8)),
                ft.Text("Chats procesados", size=14, weight=ft.FontWeight.W_600),
                ft.Row([self._search, self._filter_sender, self._only_media], wrap=True),
                self._processed_cards,
                theme.card(self._discovery_section),
                ft.Text("Registro de estado", size=14, weight=ft.FontWeight.W_600),
                self._log.view,
            ],
        )
        self._refresh_device_lock_status()

    # ---------------------------------------------------------- acciones

    def _refresh_device_lock_status(self, target_serial: Optional[str] = None) -> None:
        """Actualiza el indicador visual de pantalla y bloqueo del teléfono."""
        serial = target_serial or self._device.serial
        if not serial:
            self._screen_lock_status.value = "Pantalla: sin dispositivo"
            self._screen_lock_status.color = theme.TEXT_MUTED
            try:
                self._screen_lock_status.update()
            except Exception:
                pass
            return

        def worker():
            try:
                state = self._device.get_screen_state(serial)
                if not state.get("screen_on"):
                    txt = f"Pantalla ({serial}): En reposo (apagada)"
                    clr = theme.TEXT_MUTED
                elif state.get("is_locked"):
                    has_cred = state.get("has_credential", False)
                    txt = f"Pantalla ({serial}): Bloqueada ({'PIN de sesión listo' if has_cred else 'Sin PIN en sesión'})"
                    clr = theme.ACCENT if has_cred else theme.WARN
                else:
                    txt = f"Pantalla ({serial}): Desbloqueada y lista"
                    clr = theme.OK
                self._screen_lock_status.value = txt
                self._screen_lock_status.color = clr
                self._screen_lock_status.update()
            except Exception:
                pass

        threading.Thread(target=worker, name="wa-lock-check", daemon=True).start()

    def _ensure_device_ready_for_whatsapp(self, serial: str) -> None:
        """
        Flujo automático previo a cualquier acción del bot de WhatsApp:
        1. Identifica si la pantalla está apagada y la enciende automáticamente.
        2. Identifica si la pantalla está bloqueada.
        3. Si tiene credencial de sesión (o deslizamiento libre), la desbloquea automáticamente.
        4. Si requiere contraseña y no está configurada, informa y detiene el proceso de forma clara.
        """
        self._set_busy(True, "Verificando pantalla y desbloqueo del teléfono...", indeterminate=True)
        try:
            state = self._device.get_screen_state(serial)
        except Exception as exc:
            logger.debug("Aviso al verificar estado de pantalla en WhatsApp bot: %s", exc)
            return

        is_screen_on = state.get("screen_on", True)
        is_locked = state.get("is_locked", False)
        has_credential = state.get("has_credential", False)

        if not is_screen_on:
            self._log.append(f"[WhatsApp Bot] La pantalla de {serial} está apagada. Encendiendo automáticamente...", theme.WARN)
            wake_res = self._device.wake_device(serial)
            if wake_res.success:
                self._log.append("[WhatsApp Bot] Pantalla encendida exitosamente.", theme.OK)
            time.sleep(0.5)
            state = self._device.get_screen_state(serial)
            is_locked = state.get("is_locked", False)

        if is_locked:
            self._log.append(f"[WhatsApp Bot] Pantalla de bloqueo detectada en {serial}...", theme.WARN)
            if has_credential:
                self._log.append("[WhatsApp Bot] Desbloqueando automáticamente con credencial de sesión...", theme.OK)
                unlock_res = self._device.auto_unlock_device(serial)
                if unlock_res.success:
                    self._log.append(f"[WhatsApp Bot] Dispositivo desbloqueado: {unlock_res.message}", theme.OK)
                else:
                    self._log.append(f"[WhatsApp Bot] Aviso de desbloqueo: {unlock_res.error}", theme.WARN)
            else:
                self._log.append("[WhatsApp Bot] Intentando deslizamiento automático...", theme.TEXT_MUTED)
                unlock_res = self._device.auto_unlock_device(serial)
                if unlock_res.success:
                    self._log.append(f"[WhatsApp Bot] Dispositivo desbloqueado: {unlock_res.message}", theme.OK)
                else:
                    raise RuntimeError(
                        f"El dispositivo {serial} tiene pantalla de bloqueo activa y no cuenta con PIN configurado en la sesión.\n"
                        "Ingresa el PIN en el Administrador de Dispositivo o desbloquea manualmente tu teléfono para continuar."
                    )
        else:
            self._log.append("[WhatsApp Bot] Dispositivo encendido y desbloqueado listo.", theme.OK)

        self._refresh_device_lock_status(serial)

    def shutdown(self) -> None:
        """Cancela trabajo local en curso y limpia el handler."""
        if self._task:
            self._task.stop()
        self._detach_log_handler()

    def _on_cancel(self, _e: ft.ControlEvent) -> None:
        if self._task:
            self._task.stop()
            self._progress_text.value = "Cancelación solicitada; se detendrá al terminar la operación actual."
            self._progress_text.color = theme.WARN
            self._progress_text.update()

    def _on_process_folder(self, _e: ft.ControlEvent) -> None:
        directory = Path((self._out_dir.value or "./exports").strip())
        if not directory.is_dir():
            self._progress_text.value = f"No existe la carpeta: {directory}"
            self._progress_text.color = theme.ERR
            self._progress_text.update()
            return
        paths = [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {".txt", ".zip"}]
        if not paths:
            self._progress_text.value = "No hay exportaciones .txt o .zip en la carpeta indicada."
            self._progress_text.color = theme.WARN
            self._progress_text.update()
            return
        self._start_processing(paths)

    def _start_processing(self, paths: List[Path]) -> None:
        self._processed = []
        self._set_busy(True, "Preparando análisis de chats...", progress=0)

        def work(stop) -> None:
            for index, path in enumerate(paths, 1):
                if stop.is_set():
                    raise InterruptedError("Procesamiento cancelado por el usuario.")
                self._progress_text.value = f"[{index}/{len(paths)}] Procesando {path.name}..."
                try:
                    conversation = process_chat_file(path, my_name=(self._my_name.value or None), cancel_event=stop)
                    files = export_processed_chat(conversation, path.parent, formats=("json", "csv", "xlsx", "html"), report=True)
                    self._processed.append({"conversation": conversation, "files": files})
                except InterruptedError:
                    raise
                except Exception as exc:  # un archivo no impide el resto del lote
                    self._log.append(f"No se pudo procesar {path.name}: {exc}", theme.ERR)
                self._progress.value = index / len(paths)

        def done(error: Optional[BaseException]) -> None:
            self._refresh_processed_cards()
            if isinstance(error, InterruptedError):
                self._set_busy(False, "Procesamiento cancelado.", progress=0)
            elif error:
                self._set_busy(False, f"Falló el procesamiento: {error}", progress=0)
            else:
                self._set_busy(False, f"Procesamiento completado: {len(self._processed)}/{len(paths)} chats.", progress=1)

        self._task = BackgroundTask("whatsapp-process", target=work, on_done=done)
        self._task.start()

    def _refresh_processed_cards(self, _e: Optional[ft.ControlEvent] = None) -> None:
        query = (self._search.value or "").strip()
        sender = (self._filter_sender.value or "").strip()
        only_media = bool(self._only_media.value)
        cards: List[ft.Control] = []
        for item in self._processed:
            conversation = item["conversation"]
            matched = ChatFilter.apply(conversation.messages, sender=sender or None, text=query or None, has_media=True if only_media else None)
            if (query or sender or only_media) and not matched:
                continue
            stats = conversation.analytics
            photos = stats.media_counts_by_type.get("image", 0)
            audios = stats.media_counts_by_type.get("audio", 0)
            files = item["files"]
            actions = [
                ft.TextButton("Abrir Excel", on_click=lambda _e, p=files.get("xlsx"): self._open_path(p)),
                ft.TextButton("Abrir JSON", on_click=lambda _e, p=files.get("json"): self._open_path(p)),
                ft.TextButton("Ver chat HTML", on_click=lambda _e, p=files.get("html"): self._open_path(p)),
                ft.TextButton("Abrir carpeta", on_click=lambda _e, p=files.get("xlsx"): self._open_path(str(Path(p).parent) if p else None)),
            ]
            cards.append(theme.card(ft.Column([
                ft.Text(conversation.chat_name, weight=ft.FontWeight.W_600),
                ft.Text(f"{stats.total_messages} mensajes · enviados {stats.outgoing_count} · recibidos {stats.incoming_count} · fotos {photos} · audios {audios}"),
                ft.Row(actions, wrap=True),
            ], spacing=4)))
        self._processed_cards.controls = cards or [ft.Text("Aún no hay chats procesados.", color=theme.TEXT_MUTED)]
        try:
            self._processed_cards.update()
        except Exception:
            pass

    @staticmethod
    def _open_path(path: Optional[str]) -> None:
        if path and Path(path).exists():
            os.startfile(path)  # type: ignore[attr-defined]  # UI solo se distribuye para Windows

    def _open_discovery_folder(self, _e: Optional[ft.ControlEvent] = None) -> None:
        self._open_path(self._discovery_result.get("directory"))

    def _render_discovery(self) -> None:
        names = self._discovered_chat_names
        self._discovery_names.value = (
            "Chats detectados (usa estos n\u00fameros en 'N\u00fameros o rangos'):\n"
            + "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
            if names
            else "No se detectaron nombres de chats en las capturas."
        )
        self._discovery_gallery.controls = [
            ft.Container(
                width=230,
                content=ft.Column(
                    [
                        ft.Text(f"Vista {index}", weight=ft.FontWeight.W_600),
                        ft.Image(src=path, width=220, height=390, fit=ft.BoxFit.CONTAIN),
                        ft.TextButton("Abrir captura", on_click=lambda _e, p=path: self._open_path(p)),
                    ],
                    spacing=4,
                ),
            )
            for index, path in enumerate(self._discovery_result.get("screenshots", []), 1)
        ]
        self._discovery_section.visible = True
        try:
            self._discovery_section.update()
        except Exception:
            pass

    def _on_discover_chats(self, _e: ft.ControlEvent) -> None:
        status = self._device.get_status()
        serial = self._device.serial
        if status.requires_selection or not serial or not status.connected:
            self._progress_text.value = (
                "Selecciona un dispositivo conectado antes de explorar chats."
                if status.requires_selection
                else f"No hay dispositivo listo: {status.error or 'sin conexi\u00f3n'}"
            )
            self._progress_text.color = theme.ERR
            self._progress_text.update()
            return
        try:
            scrolls = int(self._scan_scrolls.value or "3")
            if not 3 <= scrolls <= 20:
                raise ValueError
        except (TypeError, ValueError):
            self._scan_scrolls.error_text = "Entero entre 3 y 20"
            self._scan_scrolls.update()
            return
        self._scan_scrolls.error_text = None

        out_dir = (self._out_dir.value or "./exports").strip()
        self._discovery_result = {}
        self._log.clear()
        self._attach_log_handler()
        self._set_busy(True, f"Explorando chats en {serial}...", indeterminate=True)

        def work(_stop) -> None:
            self._ensure_device_ready_for_whatsapp(serial)
            client = WhatsAppClient(DeviceManager(serial=serial), auto_watchers=True)
            try:
                with client.preserve_app_state():
                    self._discovery_result = client.discover_chats(local_dir=out_dir, scrolls=scrolls)
            finally:
                client.close()

        def done(error: Optional[BaseException]) -> None:
            self._detach_log_handler()
            if error:
                self._set_busy(False, f"No se pudo explorar WhatsApp: {error}", progress=0)
                return
            self._discovered_chat_names = list(self._discovery_result.get("chat_names", []))
            self._discovery_serial = serial
            self._render_discovery()
            self._set_busy(
                False,
                f"Exploraci\u00f3n lista: {len(self._discovered_chat_names)} chats y "
                f"{len(self._discovery_result.get('screenshots', []))} capturas.",
                progress=1,
            )

        self._task = BackgroundTask("whatsapp-discovery", target=work, on_done=done)
        self._task.start()

    def _on_install_receiver(self, _e: ft.ControlEvent) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            self._receiver_status.value = "Hay varios dispositivos conectados. Selecciona uno obligatoriamente en el panel lateral."
            self._receiver_status.color = theme.ERR
            self._receiver_status.update()
            return
        serial = self._device.serial
        if not serial:
            self._receiver_status.value = "No hay dispositivo seleccionado."
            self._receiver_status.color = theme.ERR
            self._receiver_status.update()
            return

        self._install_receiver_btn.disabled = True
        self._receiver_status.value = f"Instalando auxiliar en {serial}..."
        self._receiver_status.color = theme.WARN
        self._receiver_status.update()

        def work(_stop) -> None:
            install_receiver(serial)

        def done(error: Optional[BaseException]) -> None:
            self._install_receiver_btn.disabled = False
            if error:
                self._receiver_status.value = f"No se pudo instalar en {serial}: {error}. Se usará fallback de archivos."
                self._receiver_status.color = theme.WARN
            else:
                self._receiver_status.value = f"Auxiliar instalado en {serial}: disponible para selección automática."
                self._receiver_status.color = theme.OK
            self._receiver_status.update()
            self.view.update()

        self._receiver_task = BackgroundTask("whatsapp-receiver-install", target=work, on_done=done)
        self._receiver_task.start()

    def _on_remove_receiver(self, _e: ft.ControlEvent) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            self._receiver_status.value = "Hay varios dispositivos conectados. Selecciona uno obligatoriamente en el panel lateral."
            self._receiver_status.color = theme.ERR
            self._receiver_status.update()
            return
        serial = self._device.serial
        if not serial:
            self._receiver_status.value = "No hay dispositivo seleccionado."
            self._receiver_status.color = theme.ERR
            self._receiver_status.update()
            return

        self._remove_receiver_btn.disabled = True
        self._receiver_status.value = f"Quitando auxiliar de {serial}..."
        self._receiver_status.color = theme.WARN
        self._receiver_status.update()

        def work(_stop) -> None:
            uninstall_receiver(serial)

        def done(error: Optional[BaseException]) -> None:
            self._remove_receiver_btn.disabled = False
            if error:
                self._receiver_status.value = f"No se pudo quitar de {serial}: {error}"
                self._receiver_status.color = theme.ERR
            else:
                self._receiver_status.value = f"Auxiliar quitado del teléfono ({serial}). Exportación usará fallback de archivos."
                self._receiver_status.color = theme.TEXT_MUTED
            self._receiver_status.update()
            self.view.update()

        self._receiver_task = BackgroundTask("whatsapp-receiver-remove", target=work, on_done=done)
        self._receiver_task.start()

    def _on_check_receiver(self, _e: Optional[ft.ControlEvent] = None) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            self._receiver_status.value = "Hay varios dispositivos conectados. Selecciona uno en el panel lateral."
            self._receiver_status.color = theme.ERR
            self._receiver_status.update()
            return
        serial = self._device.serial
        if not serial:
            self._receiver_status.value = "No hay dispositivo seleccionado."
            self._receiver_status.color = theme.ERR
            self._receiver_status.update()
            return

        self._receiver_status.value = f"Comprobando auxiliar en {serial}..."
        self._receiver_status.color = theme.WARN
        self._receiver_status.update()

        check_res: List[Any] = [False, ""]

        def work(_stop) -> None:
            ready, msg = ensure_receiver_ready(serial)
            check_res[0] = ready
            check_res[1] = msg

        def done(error: Optional[BaseException]) -> None:
            if error:
                self._receiver_status.value = f"Error comprobando auxiliar: {error}"
                self._receiver_status.color = theme.ERR
            else:
                ready, msg = check_res[0], check_res[1]
                if ready:
                    self._receiver_status.value = f"Auxiliar: instalado y listo ({serial})"
                    self._receiver_status.color = theme.OK
                else:
                    self._receiver_status.value = f"Auxiliar: no disponible ({msg}). Se usará fallback de archivos (Mis archivos, Files)."
                    self._receiver_status.color = theme.WARN
            self._receiver_status.update()

        self._receiver_task = BackgroundTask("whatsapp-receiver-check", target=work, on_done=done)
        self._receiver_task.start()

    def _on_export(self, _e: ft.ControlEvent) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            self._progress_text.value = "Hay varios dispositivos conectados. Selecciona uno obligatoriamente en el panel lateral antes de exportar."
            self._progress_text.color = theme.ERR
            self._progress_text.update()
            return

        serial = self._device.serial
        if not serial or not status.connected:
            self._progress_text.value = f"No hay dispositivo conectado o listo: {status.error or 'Selecciona uno en el panel lateral'}"
            self._progress_text.color = theme.ERR
            self._progress_text.update()
            return

        try:
            n = int(self._count.value)
            if not 1 <= n <= 500:
                raise ValueError
        except (TypeError, ValueError):
            self._count.error_text = "Entero entre 1 y 500"
            self._count.update()
            return
        self._count.error_text = None

        include_media = bool(self._media.value)
        out_dir = (self._out_dir.value or "./exports").strip()
        selected_chats, inline_number_selection = split_chat_selection(self._chat_selection.value or "")
        explicit_number_selection = (self._chat_numbers.value or "").strip()
        combined_number_selection = ",".join(
            value for value in (explicit_number_selection, inline_number_selection) if value
        )
        if combined_number_selection and self._discovery_serial != serial:
            self._chat_numbers.error_text = "La lista numerada pertenece a otro dispositivo; explora este dispositivo primero."
            self._chat_numbers.update()
            return
        try:
            numbered_chats = select_discovered_chats(
                self._discovered_chat_names,
                combined_number_selection,
            )
        except ValueError as exc:
            self._chat_numbers.error_text = str(exc)
            self._chat_numbers.update()
            return
        self._chat_numbers.error_text = None
        selected_chats = list(dict.fromkeys(numbered_chats + selected_chats))
        self._last_serial, self._last_out_dir = serial, out_dir

        self._results = []
        self._processed = []
        self._current_chat_idx = 0
        self._total_chats = len(selected_chats) if selected_chats else n
        self._log.clear()
        self._attach_log_handler()
        self._set_busy(True, f"Conectando con el dispositivo seleccionado [{serial}]...", indeterminate=True)
        print(f"[UI] Exportación INICIADA (n={n}, media={include_media}, serial={serial}, destino={out_dir})")

        def work(_stop) -> None:
            # 1. Comprobar e intentar instalar el receptor auxiliar si falta
            self._set_busy(True, "Comprobando receptor auxiliar en el dispositivo...", indeterminate=True)
            self._receiver_status.value = "Comprobando auxiliar..."
            try:
                self._receiver_status.update()
            except Exception:
                pass

            clear_failed_install_cache(serial)
            ready, status_desc = ensure_receiver_ready(serial=serial)
            if not ready:
                self._receiver_status.value = f"Auxiliar no disponible ({status_desc})"
                try:
                    self._receiver_status.update()
                except Exception:
                    pass
                raise RuntimeError(
                    f"No se pudo instalar el auxiliar AndroidTools Receiver en el dispositivo ({status_desc}). "
                    "El proceso se canceló porque el auxiliar es obligatorio para la exportación."
                )

            self._receiver_status.value = f"Auxiliar: {status_desc} ({serial})"
            try:
                self._receiver_status.update()
            except Exception:
                pass

            # 2. Verificación y desbloqueo automático de pantalla antes de iniciar el bot
            self._ensure_device_ready_for_whatsapp(serial)

            client = WhatsAppClient(DeviceManager(serial=serial), auto_watchers=True)
            try:
                # Si job_queue está disponible, registrar cada chat en el historial
                with client.preserve_app_state():
                    self._results = client.export_top_chats(
                        n=n,
                        include_media=include_media,
                        local_dir=out_dir,
                        chat_names=selected_chats or None,
                    )
                # Registrar historial en JobQueue si está disponible
                if self._job_queue:
                    for r in self._results:
                        chat_name = r.get("chat_name") or r.get("chat") or "Chat"
                        is_ok = r.get("success", False)
                        jid = self._job_queue.enqueue(
                            job_type=JobType.WHATSAPP_EXPORT_CHAT,
                            title=f"Chat '{chat_name}'",
                            serial=serial,
                            payload={"chat_name": chat_name, "include_media": include_media, "local_dir": out_dir},
                            dispatch=False,
                        )
                        self._job_queue.finalize(
                            jid,
                            JobStatus.COMPLETED if is_ok else JobStatus.FAILED,
                            error=None if is_ok else r.get("failure_reason"),
                            result=r,
                        )

                # Auto-procesamiento local si está activado
                if self._auto_process.value:
                    for result in self._results:
                        if _stop.is_set():
                            raise InterruptedError("Procesamiento cancelado por el usuario.")
                        if result.get("success") and result.get("local_path"):
                            c_name = result.get("chat_name") or result.get("chat")
                            conversation = process_chat_file(
                                result["local_path"],
                                my_name=(self._my_name.value or None),
                                chat_name=c_name,
                                cancel_event=_stop,
                            )
                            files = export_processed_chat(conversation, out_dir, formats=("json", "csv", "xlsx", "html"), report=True)
                            self._processed.append({"conversation": conversation, "files": files})
            finally:
                client.close()

        self._task = BackgroundTask("whatsapp-export", target=work, on_done=self._on_done)
        self._task.start()

    def _on_retry_downloads(self, _e: ft.ControlEvent) -> None:
        pending = [
            item for item in self._results
            if not item.get("success") and item.get("remote_path") and item.get("receipt_state") == "READY"
        ]
        if not pending or not self._last_serial:
            self._retry_downloads_btn.disabled = True
            self._retry_downloads_btn.update()
            return

        self._retry_downloads_btn.disabled = True
        self._set_busy(True, f"Reintentando {len(pending)} descarga(s) confirmada(s)...", indeterminate=True)

        def work(_stop) -> None:
            client = WhatsAppClient(DeviceManager(serial=self._last_serial))
            try:
                for item in pending:
                    local_file = client.pull_exported_chat(
                        item["remote_path"], self._last_out_dir,
                        expected_size_bytes=item.get("expected_size_bytes"),
                        expected_sha256=item.get("expected_sha256"),
                    )
                    client.delete_exported_artifacts(item["remote_path"], item.get("receipt_path"))
                    item.update(success=True, verified=True, local_path=str(local_file.resolve()), error=None)
            finally:
                client.close()

        def done(error: Optional[BaseException]) -> None:
            self._retry_downloads_btn.disabled = not any(
                not item.get("success") and item.get("remote_path") and item.get("receipt_state") == "READY"
                for item in self._results
            )
            if error:
                self._log.append(f"ERROR reintentando descarga: {error}", theme.ERR)
                self._set_busy(False, "Falló el reintento de descarga.", progress=0)
            else:
                self._log.append("Descargas pendientes completadas y validadas.", theme.OK)
                self._set_busy(False, "Reintento de descargas completado.", progress=1)

        self._task = BackgroundTask("whatsapp-retry-downloads", target=work, on_done=done)
        self._task.start()

    def _on_done(self, error: Optional[BaseException]) -> None:
        self._detach_log_handler()
        ok = sum(1 for r in self._results if r.get("success"))
        self._refresh_processed_cards()
        total = len(self._results)

        # Informar detalladamente por cada chat
        for r in self._results:
            chat_name = r.get("chat_name", "Chat desconocido")
            if r.get("success"):
                method = r.get("method") or "receiver"
                target = r.get("target_used") or "AndroidTools Receiver"
                path = r.get("local_path") or ""
                self._log.append(
                    f"[OK] '{chat_name}': descargado con éxito vía {target} ({method}) -> {path}",
                    theme.OK,
                )
                if r.get("receipt_id"):
                    self._log.append(f"  Comprobante verificado: {r['receipt_id']}", theme.TEXT_MUTED)
            else:
                stage = r.get("stage_failed") or "desconocida"
                reason = r.get("failure_reason") or "Fallo no especificado"
                target = r.get("target_used")
                if stage == "file_wait" or "no_file_detected" in reason:
                    msg = f"[WARN] '{chat_name}': se seleccionó guardado"
                    if target:
                        msg += f" ({target})"
                    msg += " pero no se localizó el archivo a tiempo."
                    self._log.append(msg, theme.WARN)
                elif "timeout" in stage or "timeout" in reason.lower() or "límite" in reason.lower():
                    self._log.append(
                        f"[FAIL] '{chat_name}': cancelado por límite de tiempo/desplazamientos en etapa '{stage}': {reason}",
                        theme.ERR,
                    )
                elif stage == "receiver_conflict":
                    self._log.append(
                        f"[FAIL] '{chat_name}': cancelado por conflicto (múltiples receptores exactos): {reason}",
                        theme.ERR,
                    )
                else:
                    self._log.append(
                        f"[FAIL] '{chat_name}': cancelado en etapa '{stage}': {reason}",
                        theme.ERR,
                    )

        if error:
            self._log.append(f"ERROR DEL LOTE: {error}", theme.ERR)
            self._set_busy(False, f"Falló el lote: {error}", progress=0)
        else:
            self._set_busy(False, f"Completado: {ok}/{total} chats exportados.", progress=1 if total else 0)
            if not self._auto_process.value:
                self._log.append(
                    "Se guard\u00f3 la exportaci\u00f3n original. Activa 'Auto-procesar chats' para generar Excel, JSON y HTML.",
                    theme.WARN,
                )
            self._retry_downloads_btn.disabled = not any(
                not item.get("success") and item.get("remote_path") and item.get("receipt_state") == "READY"
                for item in self._results
            )
            self._retry_downloads_btn.update()
        print(f"[UI] Exportación FINALIZADA ({ok}/{total} OK)" + (f" error={error}" if error else ""))

    # ----------------------------------------------------------- progreso

    def _on_log(self, level: int, message: str) -> None:
        self._log.append(message, level_color(level))

        # Rastrear inicio de exportación por chat
        match = _PROGRESS_RE.search(message)
        if match:
            self._current_chat_idx, self._total_chats = int(match.group(1)), int(match.group(2))
            self._progress.value = (self._current_chat_idx - 1) / self._total_chats
            self._progress_text.value = f"[{self._current_chat_idx}/{self._total_chats}] Exportando chat..."
            try:
                self._progress.update()
                self._progress_text.update()
            except Exception:  # noqa: BLE001
                pass
            return

        # Registrar etapas activas en tiempo real
        chat_prefix = f"[{self._current_chat_idx}/{self._total_chats}] " if self._total_chats > 0 else ""
        stage_updated = False

        if "Buscando receptor" in message:
            self._progress_text.value = f"{chat_prefix}Buscando receptor AndroidTools Receiver..."
            stage_updated = True
        elif "Abriendo 'Más'" in message or "Abriendo Más" in message:
            self._progress_text.value = f"{chat_prefix}Abriendo Más en el menú Compartir..."
            stage_updated = True
        elif "Desplazando selector" in message:
            self._progress_text.value = f"{chat_prefix}Desplazando selector horizontal..."
            stage_updated = True
        elif "Probando administrador de archivos" in message or "Intentando fallback con administrador" in message:
            self._progress_text.value = f"{chat_prefix}Probando administrador de archivos..."
            stage_updated = True
        elif "Confirmando guardado" in message:
            self._progress_text.value = f"{chat_prefix}Confirmando acción de guardado..."
            stage_updated = True
        elif "Esperando" in message and ("archivo" in message.lower() or "materialización" in message.lower()):
            self._progress_text.value = f"{chat_prefix}Esperando archivo..."
            stage_updated = True
        elif "confirmación del auxiliar" in message:
            self._progress_text.value = f"{chat_prefix}Esperando confirmación del auxiliar..."
            stage_updated = True
        elif "Archivo confirmado por AndroidTools Receiver" in message:
            self._progress_text.value = f"{chat_prefix}Archivo confirmado; preparando descarga..."
            stage_updated = True
        elif "Verificando descarga" in message:
            self._progress_text.value = f"{chat_prefix}Verificando descarga..."
            stage_updated = True
        elif "Limpieza remota" in message:
            self._progress_text.value = f"{chat_prefix}Limpiando archivos temporales..."
            stage_updated = True
        elif "Estado del auxiliar:" in message:
            self._receiver_status.value = message.split("Estado del auxiliar:")[-1].strip()
            self._receiver_status.update()

        if stage_updated:
            try:
                self._progress_text.update()
            except Exception:  # noqa: BLE001
                pass

    def _set_busy(self, busy: bool, text: str, progress: Optional[float] = 0, indeterminate: bool = False) -> None:
        self._export_btn.disabled = busy
        self._discover_btn.disabled = busy
        self._process_folder_btn.disabled = busy
        self._cancel_btn.disabled = not busy
        self._count.disabled = busy
        self._chat_selection.disabled = busy
        self._chat_numbers.disabled = busy
        self._scan_scrolls.disabled = busy
        self._media.disabled = busy
        self._auto_process.disabled = busy
        self._my_name.disabled = busy
        self._out_dir.disabled = busy
        self._progress.value = None if indeterminate else progress
        self._progress_text.value = text
        try:
            self.view.update()
        except Exception:  # noqa: BLE001
            pass

    def _attach_log_handler(self) -> None:
        self._log_handler = UiLogHandler(self._on_log, LOG_PREFIXES)
        logging.getLogger().addHandler(self._log_handler)

    def _detach_log_handler(self) -> None:
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
