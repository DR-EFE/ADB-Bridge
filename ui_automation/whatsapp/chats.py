"""Módulo para listar y exportar chats recientes de WhatsApp."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Union

import uiautomator2 as u2

from .receiver import clear_failed_install_cache, ensure_receiver_ready

if TYPE_CHECKING:
    from .client import WhatsAppClient

logger = logging.getLogger("ui_automation.whatsapp.chats")

# Etiquetas comunes del sistema que no corresponden a nombres de chats reales
IGNORED_CHAT_TITLES = {
    "chats",
    "conversaciones",
    "novedades",
    "updates",
    "estados",
    "status",
    "llamadas",
    "calls",
    "comunidades",
    "communities",
    "archivados",
    "archived",
    "leídos",
    "no leídos",
    "unread",
    "favoritos",
    "favorites",
    "meta ai",
    "buscar",
    "search",
    "bloqueados",
    "blocked",
    "mensajes destacados",
    "starred messages",
}


@dataclass
class ChatExportSummary:
    """Resultado individual de la exportación de un chat."""

    chat_name: str
    success: bool
    local_path: Optional[str] = None
    method: str = "none"  # "receiver" | "file_manager" | "none"
    target_used: Optional[str] = None
    stage_failed: Optional[str] = None
    failure_reason: Optional[str] = None
    receipt_id: Optional[str] = None
    receipt_state: Optional[str] = None
    receipt_path: Optional[str] = None
    remote_path: Optional[str] = None
    expected_size_bytes: Optional[int] = None
    expected_sha256: Optional[str] = None
    verified: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WhatsAppChatManager:
    """Gestiona la lectura de chats visibles y la exportación por lotes de los primeros N chats."""

    def __init__(self, client: "WhatsAppClient"):
        self.client = client

    @property
    def d(self) -> u2.Device:
        return self.client.d

    def _extract_visible_chat_names(self) -> List[str]:
        """Extrae los nombres de chats visibles en la pantalla actual ordenados de arriba a abajo."""
        found_names: List[str] = []

        elements = self.d(resourceIdMatches=r".*id/conversations_row_contact_name.*")
        if not elements.exists:
            elements = self.d(resourceIdMatches=r".*id/conversation_contact_name.*")

        if elements.exists:
            try:
                count = elements.count
                for i in range(count):
                    el = elements[i]
                    text = el.get_text() if el.exists else ""
                    text = text.strip() if text else ""
                    if text and text.lower() not in IGNORED_CHAT_TITLES:
                        if text not in found_names:
                            found_names.append(text)
            except Exception as exc:
                logger.debug("Error iterando elementos de chat: %s", exc)

        if not found_names:
            try:
                xpath_query = (
                    "//*[@resource-id='com.whatsapp:id/conversations_row_contact_name' "
                    "or @resource-id='com.whatsapp.w4b:id/conversations_row_contact_name']"
                )
                nodes = self.d.xpath(xpath_query).all()
                for node in nodes:
                    text = (node.text or "").strip()
                    if text and text.lower() not in IGNORED_CHAT_TITLES:
                        if text not in found_names:
                            found_names.append(text)
            except Exception as exc:
                logger.debug("Error en fallback XPath de chats: %s", exc)

        return found_names

    def _swipe_chat_list(self, direction: str) -> None:
        """Desplaza la lista con fallback para versiones distintas de uiautomator2."""
        try:
            self.d.swipe_ext(direction, scale=0.55 if direction == "up" else 0.7)
        except Exception as scroll_err:
            logger.debug("Fallback de scroll %s con swipe manual: %s", direction, scroll_err)
            width, height = self.d.window_size()
            if direction == "up":
                self.d.swipe(width * 0.5, height * 0.78, width * 0.5, height * 0.30, 0.25)
            else:
                self.d.swipe(width * 0.5, height * 0.30, width * 0.5, height * 0.78, 0.25)

    def discover_chats(
        self,
        local_dir: Union[str, Path] = "./exports",
        scrolls: int = 3,
    ) -> Dict[str, Any]:
        """Guarda capturas de la lista y devuelve los chats enumerados en orden visual."""
        scrolls = max(3, min(int(scrolls), 20))
        if not self.client.ensure_chat_list():
            raise RuntimeError("No se pudo abrir la lista principal de chats de WhatsApp.")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        scan_dir = Path(local_dir).expanduser().resolve() / f"exploracion_whatsapp_{stamp}"
        scan_dir.mkdir(parents=True, exist_ok=True)
        names: List[str] = []
        seen: Set[str] = set()
        screenshots: List[str] = []
        completed_scrolls = 0

        logger.info("Explorando chats de WhatsApp con %d desplazamientos...", scrolls)
        try:
            # Captura inicial + una captura despues de cada scroll.
            for position in range(scrolls + 1):
                for name in self._extract_visible_chat_names():
                    if name not in seen:
                        seen.add(name)
                        names.append(name)

                screenshot_path = scan_dir / f"lista_{position + 1:02d}.png"
                image = self.d.screenshot()
                image.save(screenshot_path)
                screenshots.append(str(screenshot_path))
                logger.info(
                    "Captura %d/%d guardada (%d chats acumulados): %s",
                    position + 1,
                    scrolls + 1,
                    len(names),
                    screenshot_path,
                )
                if position < scrolls:
                    self._swipe_chat_list("up")
                    completed_scrolls += 1
                    time.sleep(0.8)
        finally:
            # Dejar nuevamente la lista cerca de su posicion inicial antes de que
            # el cliente restaure primer plano/chat/segundo plano.
            for _ in range(completed_scrolls):
                try:
                    self._swipe_chat_list("down")
                    time.sleep(0.2)
                except Exception:
                    break

        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "scrolls": completed_scrolls,
            "chats": [{"number": index, "name": name} for index, name in enumerate(names, 1)],
            "screenshots": screenshots,
        }
        manifest_path = scan_dir / "chats_detectados.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Exploraci\u00f3n terminada: %d chats; manifiesto %s", len(names), manifest_path)
        return {
            "directory": str(scan_dir),
            "manifest": str(manifest_path),
            "chat_names": names,
            "screenshots": screenshots,
            "scrolls": completed_scrolls,
        }

    def get_top_chat_names(self, limit: int = 10, max_scrolls: int = 8) -> List[str]:
        """
        Extrae los primeros N nombres de chats más recientes de WhatsApp.
        Realiza scroll hacia abajo de forma controlada si se solicitan más de los que caben en pantalla.
        """
        logger.info("Obteniendo los primeros %d chats de WhatsApp...", limit)

        if not self.client.ensure_chat_list():
            logger.error("No se pudo llevar WhatsApp a la lista principal de chats.")
            return []

        top_names: List[str] = []
        seen: Set[str] = set()

        for scroll_idx in range(max_scrolls + 1):
            visible_names = self._extract_visible_chat_names()
            new_added = 0

            for name in visible_names:
                if name not in seen:
                    seen.add(name)
                    top_names.append(name)
                    new_added += 1
                    if len(top_names) >= limit:
                        break

            logger.debug(
                "Scroll %d: %d nuevos chats detectados. Total acumulado: %d/%d",
                scroll_idx,
                new_added,
                len(top_names),
                limit,
            )

            if len(top_names) >= limit:
                break

            if scroll_idx > 0 and new_added == 0:
                logger.info("No se encontraron más chats adicionales tras el scroll.")
                break

            try:
                self.d.swipe_ext("up", scale=0.55)
                time.sleep(1.0)
            except Exception as scroll_err:
                logger.debug("Fallback de scroll con swipe manual: %s", scroll_err)
                w, h = self.d.window_size()
                self.d.swipe(w * 0.5, h * 0.75, w * 0.5, h * 0.30, 0.25)
                time.sleep(1.0)

        try:
            for _ in range(min(scroll_idx, 3)):
                self.d.swipe_ext("down", scale=0.7)
                time.sleep(0.3)
        except Exception:
            pass

        result = top_names[:limit]
        logger.info("Chats detectados (%d): %s", len(result), result)
        return result

    def export_top_chats(
        self,
        n: int = 10,
        include_media: bool = False,
        local_dir: Union[str, Path] = "./exports",
        chat_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Obtiene los primeros N chats y exporta cada uno de ellos descargándolo al PC
        y limpiando el archivo temporal en el teléfono.
        """
        output_dir = Path(local_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print("\n============================================================")
        print("   EXPORTACION POR LOTES DE CHATS RECIENTES DE WHATSAPP    ")
        print("============================================================")
        print(f"Cantidad objetivo : {n} chats")
        print(f"Incluir medios    : {'Sí (.zip)' if include_media else 'No (.txt)'}")
        print(f"Destino local     : {output_dir.resolve()}\n")

        # 0. Comprobar e intentar instalar el receptor auxiliar si falta
        serial = getattr(self.client, "serial", None)
        if not serial and hasattr(self.client, "device_mgr") and self.client.device_mgr:
            serial = self.client.device_mgr.serial

        clear_failed_install_cache(serial)
        is_receiver_ready, receiver_status_desc = ensure_receiver_ready(serial=serial)
        logger.info(
            "Estado del receptor auxiliar antes del lote: %s (%s)",
            "Listo" if is_receiver_ready else "No disponible",
            receiver_status_desc,
        )
        print(f"Estado auxiliar   : {receiver_status_desc}")
        if not is_receiver_ready:
            raise RuntimeError(
                f"No se pudo instalar ni verificar el auxiliar AndroidTools Receiver en el dispositivo ({receiver_status_desc}). "
                "El proceso de exportación se ha cancelado porque el auxiliar es requerido."
            )

        # 1. Asegurar WhatsApp abierto y obtener nombres
        if not self.client.ensure_chat_list():
            raise RuntimeError("WhatsApp no está disponible en primer plano.")

        requested_names = list(dict.fromkeys(name.strip() for name in (chat_names or []) if name and name.strip()))
        chat_names = requested_names[:500] if requested_names else self.get_top_chat_names(limit=n)
        if not chat_names:
            logger.warning("No se encontraron chats disponibles para exportar.")
            print("[AVISO] No se detectaron chats en la lista.")
            return []

        total = len(chat_names)
        print(f"Se identificaron {total} chat(s) para exportar:")
        for idx, name in enumerate(chat_names, 1):
            print(f"  {idx}. {name}")
        print("------------------------------------------------------------\n")

        results: List[ChatExportSummary] = []

        # 2. Iterar por cada chat realizando exportación y descarga
        for index, chat_name in enumerate(chat_names, 1):
            print(f"[{index}/{total}] Procesando chat: '{chat_name}'...")
            logger.info("[%d/%d] Iniciando exportación de '%s'...", index, total, chat_name)

            summary = ChatExportSummary(chat_name=chat_name, success=False)

            try:
                self.client.go_back_to_chat_list()

                # Ejecutar exportación estructurada
                remote_file, share_result = self.client.export_chat_structured(
                    chat_name=chat_name,
                    include_media=include_media,
                    timeout=30.0,
                )

                summary.method = share_result.method
                summary.target_used = share_result.target_used
                summary.stage_failed = share_result.stage_failed
                summary.failure_reason = share_result.failure_reason
                summary.receipt_id = share_result.receipt_id
                summary.receipt_state = share_result.receipt_state
                summary.receipt_path = share_result.receipt_path
                summary.remote_path = remote_file
                summary.expected_size_bytes = share_result.expected_size_bytes
                summary.expected_sha256 = share_result.expected_sha256

                if not remote_file:
                    err_msg = share_result.failure_reason or f"Fallo en etapa '{share_result.stage_failed}'"
                    raise RuntimeError(err_msg)

                print(f"    Archivo remoto: {remote_file} (vía {summary.target_used or summary.method})")

                # Descargar al PC vía ADB
                local_file = self.client.pull_exported_chat(
                    remote_file,
                    local_dir=output_dir,
                    expected_size_bytes=share_result.expected_size_bytes,
                    expected_sha256=share_result.expected_sha256,
                )
                summary.local_path = str(local_file.resolve())
                summary.verified = bool(share_result.receipt_id)
                summary.success = True
                print(f"    [OK] Descarga exitosa: {local_file.name} ({local_file.stat().st_size} bytes)")

                # Limpieza en el teléfono
                if share_result.receipt_id:
                    self.client.delete_exported_artifacts(remote_file, share_result.receipt_path)
                else:
                    self.client.delete_exported_file(remote_file)
                print("    Limpieza remota completada.")

            except Exception as exc:
                error_msg = str(exc)
                summary.error = error_msg
                logger.error("Error exportando chat '%s': %s", chat_name, exc)
                if summary.stage_failed:
                    print(f"    [CANCELADO] Falló en etapa '{summary.stage_failed}': {error_msg}")
                else:
                    print(f"    [ERROR] Falló exportación de '{chat_name}': {error_msg}")

            finally:
                # Regresar a la lista de chats para que el siguiente chat inicie limpio
                self.client.go_back_to_chat_list()
                results.append(summary)
                time.sleep(0.8)

        # 3. Resumen final en consola
        success_count = sum(1 for r in results if r.success)
        print("\n============================================================")
        print(f"   RESUMEN FINAL: {success_count}/{total} chats exportados exitosamente")
        print("============================================================")
        for r in results:
            status = "[OK]   " if r.success else "[FALLO]"
            details = (
                f"{r.local_path} (vía {r.target_used or r.method})"
                if r.success
                else f"Etapa: {r.stage_failed or 'desconocida'} | Error: {r.error}"
            )
            print(f"{status} {r.chat_name} -> {details}")
        print("============================================================\n")

        return [r.to_dict() for r in results]
