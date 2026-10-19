"""Módulo para la exportación nativa de chats de WhatsApp con detección robusta del menú Compartir."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import uiautomator2 as u2

from ..device import DeviceManager
from ..watchers import UIWatcherManager

logger = logging.getLogger("ui_automation.whatsapp.export")

RECEIPT_DIRECTORY = "/sdcard/Download/AndroidTools"
RECEIPT_PREFIX = "AndroidToolsReceipt_"
# El auxiliar copia el contenido compartido y calcula SHA-256 antes de publicar
# el comprobante. En dispositivos ocupados puede tardar más que el selector.
RECEIVER_RECEIPT_TIMEOUT = 75.0

# Rutas habituales de almacenamiento donde WhatsApp o el selector del sistema guardan/comparten archivos
COMMON_WHATSAPP_SEARCH_PATHS = [
    "/sdcard/Download",
    "/sdcard/Documents",
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
    "/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business/Media",
    "/sdcard/WhatsApp/Media",
    "/sdcard",
]

# Nombres accesibles de administradores de archivos conocidos para el fallback
KNOWN_FILE_MANAGERS = [
    "Mis archivos",
    "My Files",
    "Files by Google",
    "Archivos",
    "Files",
    "Administrador de archivos",
    "EX Administrador de Archivos",
    "Gestor de archivos",
    "Cx Administrador de Archivos",
    "File Manager",
    "Guardar en archivos",
    "Save to Files",
]

# Destinos prohibidos que nunca deben seleccionarse en el menú compartir
FORBIDDEN_TARGETS_REGEX = (
    r"(?i)WhatsApp|Telegram|Gmail|Outlook|Mensajes|Messages|Bluetooth|"
    r"Drive|Google Drive|Quick Share|Compartir con Nearby|Nearby Share|"
    r"Copiar al portapapeles|Copy to clipboard|Copiar|Copy"
)


class ChatExportError(Exception):
    """Excepción para errores ocurridos durante el proceso de exportación."""


@dataclass
class ShareSheetResult:
    """Resultado estructurado de la interacción con el menú Compartir / ShareSheet."""

    success: bool = False
    target_used: Optional[str] = None
    method: str = "none"  # "receiver" | "file_manager" | "none"
    scrolls_count: int = 0
    detected_path: Optional[str] = None
    failure_reason: Optional[str] = None
    stage_failed: Optional[str] = None
    receipt_id: Optional[str] = None
    receipt_path: Optional[str] = None
    receipt_state: Optional[str] = None
    expected_size_bytes: Optional[int] = None
    expected_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WhatsAppExporter:
    """Gestiona la búsqueda, exportación nativa, descarga y limpieza de chats de WhatsApp."""

    def __init__(
        self,
        device_or_manager: Union[DeviceManager, u2.Device],
        watcher_mgr: Optional[UIWatcherManager] = None,
        on_stage_callback: Optional[Callable[[str], None]] = None,
    ):
        if isinstance(device_or_manager, DeviceManager):
            self.device_mgr = device_or_manager
            self._d: Optional[u2.Device] = None
        else:
            self.device_mgr = None
            self._d = device_or_manager

        self.watcher_mgr = watcher_mgr
        self.on_stage_callback = on_stage_callback

    @property
    def d(self) -> u2.Device:
        """Obtiene la instancia activa de uiautomator2.Device."""
        if self._d is None and self.device_mgr is not None:
            self._d = self.device_mgr.get_device()
        if self._d is None:
            raise RuntimeError("No se pudo obtener una instancia válida de uiautomator2.Device.")
        return self._d

    def _notify_stage(self, stage_text: str) -> None:
        logger.info("[Etapa Exportación] %s", stage_text)
        if self.on_stage_callback:
            try:
                self.on_stage_callback(stage_text)
            except Exception:
                pass

    def open_chat_by_name(self, chat_name: str, timeout: float = 10.0) -> bool:
        """Busca un chat por nombre usando la barra de búsqueda de WhatsApp y lo abre."""
        logger.info("Buscando chat '%s'...", chat_name)

        search_icon = self.d(descriptionMatches=r"(?i)Buscar|Search")
        if not search_icon.exists:
            search_icon = self.d(resourceIdMatches=r".*id/menuitem_search.*")

        if search_icon.exists:
            search_icon.click()
            time.sleep(0.8)

        search_input = self.d(resourceIdMatches=r".*id/search_src_text.*|.*id/search_input.*")
        if not search_input.exists:
            search_input = self.d(className="android.widget.EditText")

        if search_input.exists:
            search_input.set_text(chat_name)
            time.sleep(1.2)
        else:
            logger.warning("No se encontró el campo de texto de búsqueda.")

        deadline = time.monotonic() + timeout
        chat_entry = None
        while time.monotonic() < deadline:
            target = self.d(text=chat_name)
            if not target.exists:
                target = self.d(textContains=chat_name)

            if target.exists:
                chat_entry = target
                break
            time.sleep(0.5)

        if not chat_entry or not chat_entry.exists:
            logger.error("No se encontró ningún chat que coincida con '%s'.", chat_name)
            return False

        chat_entry.click()
        time.sleep(1.0)
        logger.info("Conversación '%s' abierta exitosamente.", chat_name)
        return True

    def export_chat(
        self,
        chat_name: str,
        include_media: bool = False,
        timeout: float = 30.0,
    ) -> Optional[str]:
        """Método de compatibilidad hacia atrás: devuelve solo la ruta remota o None."""
        remote_file, _ = self.export_chat_structured(chat_name=chat_name, include_media=include_media, timeout=timeout)
        return remote_file

    def export_chat_structured(
        self,
        chat_name: str,
        include_media: bool = False,
        timeout: float = 30.0,
    ) -> Tuple[Optional[str], ShareSheetResult]:
        """
        Ejecuta el flujo completo de exportación estructurado por etapas.
        Si alguna etapa falla, recupera la pantalla de WhatsApp automáticamente.
        """
        timestamp_before = int(time.time()) - 2
        receipt_started_at_ms = int(time.time() * 1000)
        receipt_snapshot = self.list_receipt_paths()
        result = ShareSheetResult()

        # 1. Abrir chat
        self._notify_stage(f"Abriendo conversación: '{chat_name}'")
        if not self.open_chat_by_name(chat_name):
            result.stage_failed = "open_chat"
            result.failure_reason = f"No fue posible abrir el chat '{chat_name}'."
            self.recover_to_whatsapp_chat_list()
            return None, result

        # 2. Menú de opciones (3 puntos)
        self._notify_stage("Abriendo menú de opciones...")
        more_btn = self.d(descriptionMatches=r"(?i)M[áa]s opciones|More options")
        if not more_btn.exists:
            more_btn = self.d(resourceIdMatches=r".*id/more_options.*")

        if not more_btn.exists:
            w, h = self.d.window_size()
            self.d.click(w - 40, int(h * 0.05))
        else:
            more_btn.click()
        time.sleep(0.8)

        # 3. Opción "Más" / "More"
        more_item = self.d(textMatches=r"(?i)M[áa]s|More")
        if more_item.exists:
            more_item.click()
            time.sleep(0.8)

        # 4. Opción "Exportar chat"
        export_item = self.d(textMatches=r"(?i)Exportar chat|Export chat")
        if not export_item.exists:
            export_item = self.d(textContains="Exportar")
        if not export_item.exists:
            export_item = self.d(textContains="Export")

        if not export_item.exists:
            result.stage_failed = "menu_export_option"
            result.failure_reason = "No se encontró la opción 'Exportar chat' en el menú de WhatsApp."
            self.recover_to_whatsapp_chat_list()
            return None, result

        export_item.click()
        time.sleep(1.0)

        # 5. Diálogo de multimedia
        self._handle_media_selection_dialog(include_media=include_media)

        # 6. Controlador robusto de etapas del menú Compartir
        share_res = self.handle_share_sheet_flow(
            chat_name=chat_name,
            timestamp_before=timestamp_before,
            receipt_started_at_ms=receipt_started_at_ms,
            receipt_snapshot=receipt_snapshot,
            prefer_zip=include_media,
        )

        if not share_res.success:
            logger.warning(
                "Exportación de chat '%s' cancelada en etapa '%s': %s",
                chat_name,
                share_res.stage_failed,
                share_res.failure_reason,
            )
            self.recover_to_whatsapp_chat_list()
            return None, share_res

        self._notify_stage(f"Archivo exportado y detectado: {share_res.detected_path}")
        return share_res.detected_path, share_res

    def _handle_media_selection_dialog(self, include_media: bool) -> None:
        """Selecciona la opción de multimedia en el diálogo emergente de WhatsApp."""
        time.sleep(0.8)
        if include_media:
            btn = self.d(textMatches=r"(?i)Incluir archivos|Incluir multimedia|Include media")
        else:
            btn = self.d(textMatches=r"(?i)Sin archivos|Sin multimedia|Without media")

        if btn.exists:
            logger.info("Seleccionando medios: %s", "Con multimedia" if include_media else "Sin multimedia")
            btn.click()
            time.sleep(1.2)

    # -------------------------------------------------------------------------
    # Controlador de etapas del menú Compartir (ShareSheet)
    # -------------------------------------------------------------------------
    def handle_share_sheet_flow(
        self,
        chat_name: str,
        timestamp_before: int,
        receipt_started_at_ms: Optional[int] = None,
        receipt_snapshot: Optional[set[str]] = None,
        prefer_zip: bool = False,
    ) -> ShareSheetResult:
        """
        Ejecuta el controlador de etapas:
        1. Esperar menú Compartir (20s).
        2. Búsqueda inicial de AndroidTools Receiver exacta (8s).
        3. Recorrido horizontal del carrusel inferior (máximo 6 gestos).
        4. Abrir 'Más' y recorrido vertical de la lista completa.
        5. Fallback a administradores de archivos conocidos (15s).
        6. Espera de escritura y detección del archivo (15s).
        """
        result = ShareSheetResult()

        # Etapa 1: Esperar el menú Compartir (20s)
        self._notify_stage("Esperando menú Compartir...")
        if not self.detect_share_sheet(timeout=20.0):
            result.stage_failed = "wait_share_sheet"
            result.failure_reason = "No apareció el menú Compartir dentro del límite de 20 segundos."
            return result

        # Etapa 2: Búsqueda inicial del receptor exacto (8s)
        self._notify_stage("Buscando receptor...")
        target_obj, count = self._wait_for_receiver(timeout=8.0)
        if count > 1:
            result.stage_failed = "receiver_conflict"
            result.failure_reason = "Conflicto: se encontraron múltiples coincidencias exactas de AndroidTools Receiver."
            return result

        if count == 1 and target_obj:
            logger.info("Seleccionando AndroidTools Receiver directamente.")
            target_obj.click()
            result.target_used = "AndroidTools Receiver"
            result.method = "receiver"
            return self._wait_for_receiver_receipt(
                receipt_snapshot or set(), receipt_started_at_ms or int(time.time() * 1000),
                prefer_zip, result
            )

        # Etapa 3: Samsung OneUI y selectores compactos colocan las aplicaciones
        # en un carrusel horizontal inferior antes de pulsar "Más", o como lista de apps.
        self._notify_stage("Buscando receptor en el carrusel de compartir...")
        swipes_done = 0
        max_swipes = 6
        while swipes_done < max_swipes:
            swiped = self.swipe_share_sheet(direction="horizontal")
            swipes_done += 1
            result.scrolls_count = swipes_done
            time.sleep(0.8)

            target_obj, count = self.find_receiver_exact_matches()
            if count > 1:
                result.stage_failed = "receiver_conflict"
                result.failure_reason = f"Conflicto: múltiples coincidencias exactas de AndroidTools Receiver tras desplazamiento horizontal {swipes_done}."
                return result
            if count == 1 and target_obj:
                logger.info("Seleccionando AndroidTools Receiver tras desplazamiento horizontal %d.", swipes_done)
                target_obj.click()
                result.target_used = "AndroidTools Receiver"
                result.method = "receiver"
                return self._wait_for_receiver_receipt(
                    receipt_snapshot or set(), receipt_started_at_ms or int(time.time() * 1000),
                    prefer_zip, result
                )

            # Si durante el carrusel horizontal aparece el botón 'Más', salir del bucle para pulsarlo
            if self.d(textMatches=r"(?i)^M[áa]s$|^More$").exists or self.d(descriptionMatches=r"(?i)^M[áa]s$|^More$").exists:
                break

            if not swiped:
                break

        # Etapa 4: La vista abierta con "Más" (o menú expandido) se desplaza verticalmente y horizontalmente
        self._notify_stage("Abriendo Más...")
        if self.open_more_targets():
            time.sleep(1.0)
            # Primero intentar scrolls verticales
            for v_swipes in range(0, max_swipes + 1):
                target_obj, count = self.find_receiver_exact_matches()
                if count > 1:
                    result.stage_failed = "receiver_conflict"
                    result.failure_reason = "Conflicto: se encontraron múltiples coincidencias exactas de AndroidTools Receiver tras abrir Más."
                    return result
                if count == 1 and target_obj:
                    logger.info("Seleccionando AndroidTools Receiver desde la lista completa.")
                    target_obj.click()
                    result.target_used = "AndroidTools Receiver"
                    result.method = "receiver"
                    return self._wait_for_receiver_receipt(
                        receipt_snapshot or set(), receipt_started_at_ms or int(time.time() * 1000),
                        prefer_zip, result
                    )
                if v_swipes == max_swipes:
                    break
                if not self.swipe_share_sheet(direction="vertical"):
                    break
                result.scrolls_count += 1
                time.sleep(0.8)

            # Si aún no aparece, algunos dispositivos abren un segundo carrusel o cuadrícula horizontal
            for h_swipes in range(1, 4):
                if not self.swipe_share_sheet(direction="horizontal"):
                    break
                result.scrolls_count += 1
                time.sleep(0.8)
                target_obj, count = self.find_receiver_exact_matches()
                if count == 1 and target_obj:
                    logger.info("Seleccionando AndroidTools Receiver tras desplazamiento horizontal secundario.")
                    target_obj.click()
                    result.target_used = "AndroidTools Receiver"
                    result.method = "receiver"
                    return self._wait_for_receiver_receipt(
                        receipt_snapshot or set(), receipt_started_at_ms or int(time.time() * 1000),
                        prefer_zip, result
                    )

        # Etapa 5: Fallback con administradores de archivos conocidos
        self._notify_stage("Probando administrador de archivos...")
        fm_target, fm_name = self.find_file_manager_target()
        if fm_target:
            logger.info("Seleccionando administrador de archivos compatible: '%s'", fm_name)
            fm_target.click()
            result.target_used = fm_name
            result.method = "file_manager"

            # Esperar confirmación de guardado (15s)
            self._notify_stage(f"Confirmando guardado en {fm_name}...")
            self.confirm_file_save(timeout=15.0)

            return self._wait_and_detect_file(chat_name, timestamp_before, prefer_zip, result)

        # Si llegamos aquí, no hubo receptor ni administrador de archivos
        result.stage_failed = "no_compatible_target"
        result.failure_reason = "No se encontró AndroidTools Receiver ni una aplicación de archivos compatible."
        return result

    def detect_share_sheet(self, timeout: float = 20.0) -> bool:
        """Detecta la presencia del menú Compartir mediante identificadores y textos localizados."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # 1. Identificadores de share sheet de Android
            if self.d(resourceIdMatches=r".*id/chooser.*|.*id/resolver_list.*|.*id/share_sheet.*|.*id/content_preview.*").exists:
                return True

            # 2. Textos característicos
            if self.d(textMatches=r"(?i)^Compartir$|^Share$|^Compartir con$|^Share with$|^Aplicaciones$|^Apps$|^Direct share$|^Frecuentes$").exists:
                return True

            # 3. Presencia de opciones conocidas
            if self.d(text="AndroidTools Receiver").exists:
                return True
            for fm in KNOWN_FILE_MANAGERS:
                if self.d(textMatches=r"(?i)^" + re.escape(fm) + r"$").exists:
                    return True

            time.sleep(0.5)

        return False

    def find_receiver_exact_matches(self) -> Tuple[Optional[u2.UiObject], int]:
        """
        Busca AndroidTools Receiver exigiendo coincidencia exacta.
        Devuelve (target, total_coincidencias).
        """
        matches: List[u2.UiObject] = []
        exact_name = "AndroidTools Receiver"

        # Buscar por texto exacto
        text_nodes = self.d(text=exact_name)
        if text_nodes.exists:
            count = text_nodes.count
            for i in range(count):
                matches.append(text_nodes[i])

        # Buscar por contentDescription exacta
        desc_nodes = self.d(description=exact_name)
        if desc_nodes.exists:
            count = desc_nodes.count
            for i in range(count):
                node = desc_nodes[i]
                try:
                    # Evitar duplicar el mismo nodo por bounds
                    bounds = node.info.get("bounds")
                    if not any(m.info.get("bounds") == bounds for m in matches):
                        matches.append(node)
                except Exception:
                    matches.append(node)

        total = len(matches)
        target = matches[0] if total == 1 else None
        return target, total

    def _wait_for_receiver(self, timeout: float = 8.0) -> Tuple[Optional[u2.UiObject], int]:
        """Espera de forma no bloqueante hasta timeout por el receptor exacto."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            target, count = self.find_receiver_exact_matches()
            if count > 0:
                return target, count
            time.sleep(0.5)
        return None, 0

    def open_more_targets(self) -> bool:
        """Intenta abrir la opción 'Más' / 'More' en el selector de compartir (1 intento)."""
        more_btn = self.d(textMatches=r"(?i)^M[áa]s$|^More$")
        if not more_btn.exists:
            more_btn = self.d(descriptionMatches=r"(?i)^M[áa]s$|^More$")
        if not more_btn.exists:
            more_btn = self.d(resourceIdMatches=r".*id/more.*")

        if more_btn.exists:
            try:
                more_btn.click()
                logger.info("Pulsado botón 'Más' del selector.")
                return True
            except Exception as exc:
                logger.debug("Error pulsando botón Más: %s", exc)
        return False

    def swipe_share_sheet(self, direction: str = "horizontal") -> bool:
        """
        Realiza un desplazamiento en el selector. El selector compacto usa un
        carrusel horizontal (especialmente en OneUI de Samsung); la lista abierta mediante "Más"
        o lista expandida puede requerir scroll vertical o carrusel horizontal de apps.
        Prioriza selectores accesibles antes de usar coordenadas calculadas.
        """
        if direction not in {"horizontal", "vertical"}:
            raise ValueError("direction debe ser 'horizontal' o 'vertical'")

        # 1. Intentar scroll accesible sobre contenedor deslizable específico
        try:
            # Buscar contenedores horizontales típicos de OneUI / Android Chooser
            if direction == "horizontal":
                horiz_containers = [
                    self.d(scrollable=True, classNameMatches=r".*HorizontalScrollView.*|.*RecyclerView.*|.*ViewPager.*"),
                    self.d(resourceIdMatches=r".*id/resolver_list.*|.*id/chooser.*|.*id/app_list.*|.*id/share_target.*", scrollable=True),
                    self.d(scrollable=True),
                ]
                for c in horiz_containers:
                    if c.exists:
                        try:
                            c.scroll.horiz.forward(steps=40)
                            return True
                        except Exception:
                            continue
            else:
                scrollable = self.d(scrollable=True)
                if scrollable.exists:
                    scrollable.scroll.vert.forward(steps=40)
                    return True
        except Exception as exc:
            logger.debug("Scroll accesible no disponible o falló: %s", exc)

        # 2. Gesto adaptativo por coordenadas.
        # En Samsung OneUI, el carrusel de aplicaciones en el Share Sheet se ubica
        # en el tercio inferior (típicamente entre 70% y 92% de la pantalla).
        try:
            w, h = self.d.window_size()
            if direction == "horizontal":
                # Si encontramos algún icono o texto de app en la hoja de compartir,
                # usamos su coordenada Y para deslizar exactamente sobre la fila de apps
                target_y = None
                sample_apps = self.d(resourceIdMatches=r".*id/text1.*|.*id/title.*|.*id/icon.*")
                if sample_apps.exists and sample_apps.count > 0:
                    try:
                        bounds = sample_apps[0].info.get("bounds")
                        if bounds and "top" in bounds and "bottom" in bounds:
                            target_y = (bounds["top"] + bounds["bottom"]) // 2
                    except Exception:
                        pass

                # Fallback de altura Y si no se pudo determinar con precisión
                if not target_y or target_y < int(h * 0.40):
                    target_y = int(h * 0.82)

                start_x = int(w * 0.88)
                end_x = int(w * 0.12)
                logger.info("Ejecutando swipe horizontal en ShareSheet: (%d, %d) -> (%d, %d)", start_x, target_y, end_x, target_y)
                self.d.swipe(start_x, target_y, end_x, target_y, 0.25)
                # Intento adicional con paso ligeramente diferente para asegurar arrastre
                time.sleep(0.3)
            else:
                x = int(w * 0.50)
                start_y = int(h * 0.78)
                end_y = int(h * 0.30)
                logger.info("Ejecutando swipe vertical en ShareSheet: (%d, %d) -> (%d, %d)", x, start_y, x, end_y)
                self.d.swipe(x, start_y, x, end_y, 0.30)
            return True
        except Exception as exc:
            logger.debug("Gesto swipe por coordenadas falló: %s", exc)
            return False

    def find_file_manager_target(self) -> Tuple[Optional[u2.UiObject], Optional[str]]:
        """Busca aplicaciones de administración de archivos reconocidas evitando mensajería/nube."""
        for name in KNOWN_FILE_MANAGERS:
            # 1. Por texto exacto o regex
            node = self.d(textMatches=r"(?i)^" + re.escape(name) + r"$")
            if not node.exists:
                node = self.d(descriptionMatches=r"(?i)^" + re.escape(name) + r"$")

            if node.exists:
                # Verificar que el texto no coincida con destinos prohibidos
                text_val = node.get_text() or ""
                desc_val = node.info.get("contentDescription") or ""
                combined = f"{text_val} {desc_val}".strip()
                if not re.search(FORBIDDEN_TARGETS_REGEX, combined):
                    return node, name

        return None, None

    def confirm_file_save(self, timeout: float = 15.0) -> bool:
        """
        Confirma únicamente acciones de guardado legítimas en el administrador de archivos
        ('Guardar', 'Save', 'Listo', 'Done', 'Usar esta carpeta', 'Permitir', 'Seleccionar').
        """
        deadline = time.monotonic() + timeout
        confirm_regex = (
            r"(?i)^Guardar$|^Save$|^Listo$|^Done$|^Usar esta carpeta$|"
            r"^Use this folder$|^Permitir$|^Allow$|^Seleccionar$|^Select$"
        )
        while time.monotonic() < deadline:
            btn = self.d(textMatches=confirm_regex)
            if not btn.exists:
                btn = self.d(descriptionMatches=confirm_regex)

            if btn.exists:
                logger.info("Confirmando acción de guardado en diálogo de archivos.")
                try:
                    btn.click()
                    time.sleep(1.0)
                    return True
                except Exception as exc:
                    logger.debug("Error al hacer clic en botón de guardado: %s", exc)

            time.sleep(0.5)

        return False

    def _wait_and_detect_file(
        self,
        chat_name: str,
        timestamp_before: int,
        prefer_zip: bool,
        result: ShareSheetResult,
        timeout: float = 15.0,
    ) -> ShareSheetResult:
        """Etapa 6: Espera a que el archivo se escriba y lo localiza en el almacenamiento."""
        self._notify_stage("Esperando archivo...")
        deadline = time.monotonic() + timeout
        remote_file = None

        while time.monotonic() < deadline and not remote_file:
            remote_file = self.find_exported_chat_file(
                chat_name=chat_name,
                min_mtime=timestamp_before,
                prefer_zip=prefer_zip,
            )
            if not remote_file:
                time.sleep(0.8)

        if remote_file:
            result.success = True
            result.detected_path = remote_file
            return result

        result.success = False
        result.stage_failed = "wait_file_write"
        result.failure_reason = (
            f"El chat fue enviado vía {result.target_used or 'selector'} pero el archivo "
            "no se materializó en el almacenamiento dentro de los 15 segundos."
        )
        return result

    def recover_to_whatsapp_chat_list(self, max_attempts: int = 5) -> bool:
        """
        Regresa siempre a la pantalla principal de chats de WhatsApp ante cualquier fallo.
        Presiona botón Up o Back hasta alcanzar la lista principal.
        """
        logger.info("Recuperando estado de WhatsApp para regresar a la lista de chats...")
        for attempt in range(1, max_attempts + 1):
            # 1. Comprobar si ya estamos en la lista de chats
            if self.d(text="Chats").exists or self.d(description="Nuevo chat").exists or self.d(description="New chat").exists:
                logger.info("Lista de chats alcanzada exitosamente.")
                return True

            # 2. Intentar botón 'Navegar hacia arriba'
            up_btn = self.d(descriptionMatches=r"(?i)Navegar hacia arriba|Navigate up")
            if up_btn.exists:
                try:
                    up_btn.click()
                    time.sleep(0.6)
                    continue
                except Exception:
                    pass

            # 3. Presionar tecla Back física
            try:
                self.d.press("back")
                time.sleep(0.6)
            except Exception as exc:
                logger.debug("Error enviando tecla back: %s", exc)

        # Si aún no regresa, forzar apertura de la actividad principal de WhatsApp
        try:
            logger.info("Reabriendo WhatsApp para garantizar estado limpio...")
            self.d.app_start("com.whatsapp")
            time.sleep(1.0)
        except Exception:
            try:
                self.d.app_start("com.whatsapp.w4b")
                time.sleep(1.0)
            except Exception:
                pass

        return True

    # -------------------------------------------------------------------------
    # Búsqueda y gestión de archivos remotos
    # -------------------------------------------------------------------------
    def list_receipt_paths(self) -> set[str]:
        """Lista comprobantes del auxiliar sin escanear todo el teléfono."""
        command = f"find {shlex.quote(RECEIPT_DIRECTORY)} -maxdepth 1 -type f -name '{RECEIPT_PREFIX}*.json' 2>/dev/null"
        try:
            output = self.d.shell(command).output or ""
            return {line.strip() for line in output.splitlines() if line.strip().startswith(RECEIPT_DIRECTORY + "/")}
        except Exception as exc:
            logger.debug("No se pudieron listar comprobantes: %s", exc)
            return set()

    def _read_receipt(self, receipt_path: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(self.d.shell(f"cat {shlex.quote(receipt_path)}").output or "")
        except Exception as exc:
            logger.debug("Comprobante aún no legible %s: %s", receipt_path, exc)
            return None
        required = ("id", "state", "fileName", "remotePath", "sizeBytes", "sha256", "createdAtEpochMs")
        if not isinstance(payload, dict) or any(key not in payload for key in required):
            return None
        remote_path, file_name = str(payload["remotePath"]), str(payload["fileName"])
        if not remote_path.startswith(RECEIPT_DIRECTORY + "/") or Path(remote_path).name != file_name:
            logger.warning("Comprobante con ruta inválida ignorado: %s", receipt_path)
            return None
        return payload

    def _wait_for_receiver_receipt(
        self, known_receipts: set[str], started_at_ms: int, prefer_zip: bool,
        result: ShareSheetResult, timeout: float = RECEIVER_RECEIPT_TIMEOUT,
    ) -> ShareSheetResult:
        """Espera el comprobante nuevo del auxiliar, sin buscar archivos ajenos."""
        self._notify_stage("Esperando confirmación del auxiliar...")
        extension = ".zip" if prefer_zip else ".txt"

        def consume_receipt(paths: set[str]) -> bool:
            """Actualiza ``result`` si alguno de los comprobantes es válido."""
            for receipt_path in sorted(paths):
                receipt = self._read_receipt(receipt_path)
                if not receipt:
                    continue
                try:
                    created_at, size = int(receipt["createdAtEpochMs"]), int(receipt["sizeBytes"])
                except (TypeError, ValueError):
                    continue
                remote_path = str(receipt["remotePath"])
                if created_at < started_at_ms - 2000 or not remote_path.lower().endswith(extension):
                    continue
                result.receipt_id = str(receipt["id"])
                result.receipt_path = receipt_path
                result.receipt_state = str(receipt["state"])
                result.expected_size_bytes = size
                result.expected_sha256 = str(receipt["sha256"])
                if result.receipt_state == "FAILED":
                    result.stage_failed = "receiver_copy"
                    result.failure_reason = str(receipt.get("error") or "El auxiliar no pudo copiar el archivo compartido.")
                    return True
                if result.receipt_state != "READY":
                    continue
                if size <= 0 or not re.fullmatch(r"[0-9a-fA-F]{64}", result.expected_sha256 or ""):
                    result.stage_failed = "receiver_receipt_invalid"
                    result.failure_reason = "El comprobante no contiene tamaño o hash válidos."
                    return True
                result.success, result.detected_path = True, remote_path
                self._notify_stage("Archivo confirmado por AndroidTools Receiver.")
                return True
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if consume_receipt(self.list_receipt_paths() - known_receipts):
                return result
            time.sleep(0.7)

        # Algunos proveedores de almacenamiento publican el archivo y su
        # entrada MediaStore con retraso. Antes de fallar, releer todos los
        # comprobantes y filtrar estrictamente por hora y extensión; esto no
        # puede aceptar un comprobante antiguo de otro chat.
        if consume_receipt(self.list_receipt_paths()):
            return result
        result.stage_failed = "wait_receiver_receipt"
        result.failure_reason = f"AndroidTools Receiver no publicó un comprobante dentro de {int(timeout)} segundos."
        return result

    def find_exported_chat_file(
        self,
        chat_name: Optional[str] = None,
        min_mtime: Optional[int] = None,
        prefer_zip: bool = False,
    ) -> Optional[str]:
        """Busca en las rutas comunes del teléfono el archivo exportado (.txt o .zip) más reciente."""
        logger.info("Buscando archivo de chat exportado en el dispositivo...")
        extension = ".zip" if prefer_zip else ".txt"

        clean_name = re.sub(r"[^\w\s-]", "", chat_name).strip() if chat_name else ""
        candidate_files: List[Tuple[str, int]] = []

        for folder in COMMON_WHATSAPP_SEARCH_PATHS:
            cmd = f"find {folder} -maxdepth 3 -type f \\( -name '*{extension}' -o -name '*WhatsApp*' \\) 2>/dev/null"
            try:
                out = self.d.shell(cmd).output or ""
                lines = [line.strip() for line in out.splitlines() if line.strip()]
                for fpath in lines:
                    fname = os.path.basename(fpath).lower()
                    if not (fname.endswith(".txt") or fname.endswith(".zip")):
                        continue

                    if clean_name and clean_name.lower() not in fname and "chat" not in fname and "whatsapp" not in fname:
                        continue

                    stat_out = self.d.shell(f"stat -c %Y '{fpath}' 2>/dev/null").output or ""
                    try:
                        mtime = int(stat_out.strip())
                    except ValueError:
                        mtime = 0

                    if min_mtime and mtime < min_mtime:
                        continue

                    candidate_files.append((fpath, mtime))
            except Exception as exc:
                logger.debug("Error buscando en %s: %s", folder, exc)

        if not candidate_files:
            return None

        candidate_files.sort(key=lambda x: x[1], reverse=True)
        return candidate_files[0][0]

    def pull_exported_chat(
        self,
        remote_path: str,
        local_dir: Union[str, Path] = "./exports",
        expected_size_bytes: Optional[int] = None,
        expected_sha256: Optional[str] = None,
    ) -> Path:
        """Descarga el archivo exportado desde el teléfono a la computadora local vía ADB."""
        dest_dir = Path(local_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(remote_path).name
        local_target = dest_dir / filename

        logger.info("Descargando archivo desde teléfono: %s -> %s", remote_path, local_target)
        try:
            self.d.pull(remote_path, str(local_target))
            if not local_target.exists() or local_target.stat().st_size == 0:
                raise ChatExportError(f"El archivo descargado está vacío o no se creó: {local_target}")

            if expected_size_bytes is not None and local_target.stat().st_size != expected_size_bytes:
                raise ChatExportError(f"Tamaño inválido: se esperaban {expected_size_bytes} bytes y se recibieron {local_target.stat().st_size}.")
            if expected_sha256:
                self._notify_stage("Verificando descarga...")
                digest = hashlib.sha256()
                with local_target.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest().lower() != expected_sha256.lower():
                    raise ChatExportError("La validación SHA-256 del archivo descargado falló.")

            logger.info("Archivo descargado y validado: %s (%.2f KB)", local_target, local_target.stat().st_size / 1024)
            return local_target
        except Exception as exc:
            try:
                if local_target.exists():
                    local_target.unlink()
            except OSError:
                pass
            msg = f"Fallo al descargar '{remote_path}' a '{local_target}': {exc}"
            logger.error(msg)
            raise ChatExportError(msg) from exc

    def delete_exported_artifacts(self, remote_path: str, receipt_path: Optional[str] = None) -> bool:
        """Elimina archivo y comprobante tras la validación local."""
        self._notify_stage("Limpieza remota...")
        return all(self.delete_exported_file(path) for path in (remote_path, receipt_path) if path)

    def delete_exported_file(self, remote_path: str) -> bool:
        """Elimina el archivo exportado del dispositivo Android para limpieza y privacidad."""
        logger.info("Limpiando archivo del dispositivo: %s", remote_path)
        try:
            safe_path = remote_path.replace("'", "'\\''")
            self.d.shell(f"rm -f '{safe_path}'")
            logger.info("Archivo remoto eliminado correctamente.")
            return True
        except Exception as exc:
            logger.warning("No se pudo eliminar el archivo remoto %s: %s", remote_path, exc)
            return False
