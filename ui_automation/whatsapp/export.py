"""Módulo para la exportación nativa de chats de WhatsApp."""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

import uiautomator2 as u2

from ..device import DeviceManager
from ..watchers import UIWatcherManager

logger = logging.getLogger("ui_automation.whatsapp.export")

# Rutas habituales de almacenamiento donde WhatsApp o el selector del sistema guardan/comparten archivos
COMMON_WHATSAPP_SEARCH_PATHS = [
    "/sdcard/Download",
    "/sdcard/Documents",
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
    "/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business/Media",
    "/sdcard/WhatsApp/Media",
    "/sdcard",
]


class ChatExportError(Exception):
    """Excepción para errores ocurridos durante el proceso de exportación."""
    pass


class WhatsAppExporter:
    """Gestiona la búsqueda, exportación nativa, descarga y limpieza de chats de WhatsApp."""

    def __init__(
        self,
        device_or_manager: Union[DeviceManager, u2.Device],
        watcher_mgr: Optional[UIWatcherManager] = None,
    ):
        if isinstance(device_or_manager, DeviceManager):
            self.device_mgr = device_or_manager
            self._d: Optional[u2.Device] = None
        else:
            self.device_mgr = None
            self._d = device_or_manager

        self.watcher_mgr = watcher_mgr

    @property
    def d(self) -> u2.Device:
        """Obtiene la instancia activa de uiautomator2.Device."""
        if self._d is None and self.device_mgr is not None:
            self._d = self.device_mgr.get_device()
        if self._d is None:
            raise RuntimeError("No se pudo obtener una instancia válida de uiautomator2.Device.")
        return self._d

    def open_chat_by_name(self, chat_name: str, timeout: float = 10.0) -> bool:
        """
        Busca un chat por nombre usando la barra de búsqueda de WhatsApp y lo abre.

        :param chat_name: Nombre exacto o parcial del contacto o grupo.
        :param timeout: Tiempo máximo de espera.
        :return: True si logró abrir la conversación, False en caso contrario.
        """
        logger.info("Buscando chat '%s'...", chat_name)

        # 1. Hacer clic en el icono de búsqueda
        search_icon = self.d(descriptionMatches=r"(?i)Buscar|Search")
        if not search_icon.exists:
            search_icon = self.d(resourceIdMatches=r".*id/menuitem_search.*")

        if search_icon.exists:
            search_icon.click()
            time.sleep(0.8)
        else:
            logger.debug("Icono de búsqueda no visible directamente. Intentando búsqueda directa en pantalla...")

        # 2. Localizar campo de entrada de búsqueda y escribir
        search_input = self.d(resourceIdMatches=r".*id/search_src_text.*|.*id/search_input.*")
        if not search_input.exists:
            search_input = self.d(className="android.widget.EditText")

        if search_input.exists:
            search_input.set_text(chat_name)
            time.sleep(1.2)
        else:
            logger.warning("No se encontró el campo de texto de búsqueda.")

        # 3. Hacer clic en el resultado del chat
        deadline = time.monotonic() + timeout
        chat_entry = None
        while time.monotonic() < deadline:
            # Buscar por texto exacto o que contenga el nombre
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
        """
        Ejecuta el flujo nativo de exportación de chat en WhatsApp:
        Abre el chat -> Menú (3 puntos) -> Más -> Exportar chat ->
        Selecciona incluir/excluir medios -> Espera generación -> Identifica archivo.

        :param chat_name: Nombre del chat a exportar.
        :param include_media: True para incluir archivos multimedia (.zip), False para solo texto (.txt).
        :param timeout: Tiempo máximo de espera para la generación del archivo.
        :return: Ruta remota en el dispositivo del archivo generado, o None si no se completó.
        """
        # Registrar marca de tiempo antes de exportar para identificar el archivo nuevo
        timestamp_before = int(time.time()) - 2

        # 1. Abrir la conversación
        if not self.open_chat_by_name(chat_name):
            raise ChatExportError(f"No fue posible abrir el chat '{chat_name}' para exportar.")

        # 2. Abrir el menú de opciones (3 puntos verticales)
        logger.info("Abriendo menú de opciones del chat...")
        more_btn = self.d(descriptionMatches=r"(?i)M[áa]s opciones|More options")
        if not more_btn.exists:
            more_btn = self.d(resourceIdMatches=r".*id/more_options.*")

        if not more_btn.exists:
            # Coordenadas aproximadas de la esquina superior derecha si no hay selector accesible
            w, h = self.d.window_size()
            self.d.click(w - 40, int(h * 0.05))
        else:
            more_btn.click()
        time.sleep(0.8)

        # 3. Encontrar y hacer clic en "Más" / "More"
        more_item = self.d(textMatches=r"(?i)M[áa]s|More")
        if more_item.exists:
            more_item.click()
            time.sleep(0.8)

        # 4. Encontrar y hacer clic en "Exportar chat" / "Export chat"
        export_item = self.d(textMatches=r"(?i)Exportar chat|Export chat")
        if not export_item.exists:
            # Puede aparecer directamente en el primer nivel del menú en algunas versiones
            export_item = self.d(textContains="Exportar")
        if not export_item.exists:
            export_item = self.d(textContains="Export")

        if not export_item.exists:
            raise ChatExportError("No se encontró la opción 'Exportar chat' en el menú de WhatsApp.")

        export_item.click()
        time.sleep(1.0)

        # 5. Manejar diálogo de inclusión de medios ("Sin archivos de medios" / "Incluir archivos de medios")
        self._handle_media_selection_dialog(include_media=include_media)

        # 6. Esperar a que se procese la exportación y aparezca el diálogo del sistema
        logger.info("Esperando a que WhatsApp termine de empaquetar el chat...")
        self._handle_share_or_save_dialog(timeout=timeout)

        # 7. Localizar el archivo generado en el almacenamiento del dispositivo
        remote_file = self.find_exported_chat_file(
            chat_name=chat_name,
            min_mtime=timestamp_before,
            prefer_zip=include_media,
        )

        if remote_file:
            logger.info("Archivo exportado detectado en el dispositivo: %s", remote_file)
            return remote_file

        logger.warning(
            "La exportación finalizó pero no se detectó la ruta exacta por escaneo. "
            "Se devuelve confirmación de flujo completado."
        )
        return None

    def _handle_media_selection_dialog(self, include_media: bool) -> None:
        """Selecciona la opción de multimedia en el diálogo emergente de WhatsApp."""
        time.sleep(0.8)
        if include_media:
            btn = self.d(textMatches=r"(?i)Incluir archivos|Incluir multimedia|Include media")
        else:
            btn = self.d(textMatches=r"(?i)Sin archivos|Sin multimedia|Without media")

        if btn.exists:
            logger.info("Seleccionando opción de medios: %s", "Con multimedia" if include_media else "Sin multimedia")
            btn.click()
            time.sleep(1.5)

    def _handle_share_or_save_dialog(self, timeout: float = 20.0) -> None:
        """
        Gestiona el diálogo del sistema (Android ShareSheet o Selector de Guardado)
        que aparece inmediatamente después de exportar.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Si aparece un diálogo de 'Guardar en archivos' o similar, intentar guardar
            save_option = self.d(textMatches=r"(?i)Guardar en archivos|Save to Files|Total Commander|Drive")
            if save_option.exists:
                logger.info("Selector de destino detectado en pantalla.")
                break

            # Si el selector del sistema (ShareSheet) está visible
            if self.d(resourceIdMatches=r".*id/chooser.*|.*android:id/resolver_list.*").exists:
                logger.info("Menú para compartir de Android detectado.")
                # Presionar atrás para cerrar la cortina de compartir si el archivo ya fue escrito a caché
                self.d.press("back")
                break

            time.sleep(0.8)

    def find_exported_chat_file(
        self,
        chat_name: Optional[str] = None,
        min_mtime: Optional[int] = None,
        prefer_zip: bool = False,
    ) -> Optional[str]:
        """
        Busca en las rutas comunes del teléfono el archivo exportado (.txt o .zip) más reciente.

        :param chat_name: Nombre del chat para filtrar el nombre del archivo.
        :param min_mtime: Epoch timestamp mínimo de modificación.
        :param prefer_zip: Si es True, prioriza archivos .zip; si es False, .txt.
        :return: Ruta absoluta del archivo en el dispositivo, o None si no se encontró.
        """
        logger.info("Buscando archivo de chat exportado en el dispositivo...")
        extension = ".zip" if prefer_zip else ".txt"

        clean_name = re.sub(r"[^\w\s-]", "", chat_name).strip() if chat_name else ""
        candidate_files: List[Tuple[str, int]] = []

        # Usar find de shell de Android para buscar archivos recientes
        for folder in COMMON_WHATSAPP_SEARCH_PATHS:
            cmd = f"find {folder} -maxdepth 3 -type f \\( -name '*{extension}' -o -name '*WhatsApp*' \\) 2>/dev/null"
            try:
                out = self.d.shell(cmd).output or ""
                lines = [line.strip() for line in out.splitlines() if line.strip()]
                for fpath in lines:
                    # Comprobar que sea un archivo de chat de WhatsApp
                    fname = os.path.basename(fpath).lower()
                    if not (fname.endswith(".txt") or fname.endswith(".zip")):
                        continue

                    if clean_name and clean_name.lower() not in fname and "chat" not in fname and "whatsapp" not in fname:
                        continue

                    # Obtener timestamp de modificación
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

        # Ordenar por el más reciente
        candidate_files.sort(key=lambda x: x[1], reverse=True)
        newest_file = candidate_files[0][0]
        return newest_file

    def pull_exported_chat(
        self,
        remote_path: str,
        local_dir: Union[str, Path] = "./exports",
    ) -> Path:
        """
        Descarga el archivo exportado desde el teléfono a la computadora local vía ADB.

        :param remote_path: Ruta del archivo en el dispositivo (ej: '/sdcard/Download/Chat.txt').
        :param local_dir: Carpeta local de destino.
        :return: Path local del archivo descargado.
        :raises ChatExportError: Si no se pudo transferir el archivo.
        """
        dest_dir = Path(local_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(remote_path).name
        local_target = dest_dir / filename

        logger.info("Descargando archivo desde teléfono: %s -> %s", remote_path, local_target)
        try:
            # uiautomator2 Device.pull transfiere el archivo
            self.d.pull(remote_path, str(local_target))
            if not local_target.exists() or local_target.stat().st_size == 0:
                raise ChatExportError(f"El archivo descargado está vacío o no se creó: {local_target}")

            logger.info("Archivo descargado exitosamente: %s (%.2f KB)", local_target, local_target.stat().st_size / 1024)
            return local_target
        except Exception as exc:
            msg = f"Fallo al descargar '{remote_path}' a '{local_target}': {exc}"
            logger.error(msg)
            raise ChatExportError(msg) from exc

    def delete_exported_file(self, remote_path: str) -> bool:
        """
        Elimina el archivo exportado del dispositivo Android para limpieza y privacidad.

        :param remote_path: Ruta remota en el dispositivo a eliminar.
        :return: True si se eliminó correctamente.
        """
        logger.info("Limpiando archivo del dispositivo: %s", remote_path)
        try:
            # Sanitizar comillas simples para evitar inyección en shell
            safe_path = remote_path.replace("'", "'\\''")
            res = self.d.shell(f"rm -f '{safe_path}'")
            logger.info("Archivo remoto eliminado correctamente.")
            return True
        except Exception as exc:
            logger.warning("No se pudo eliminar el archivo remoto %s: %s", remote_path, exc)
            return False
