"""Cliente para automatización y navegación en WhatsApp y WhatsApp Business."""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Union

import uiautomator2 as u2

from ..device import DeviceManager
from ..watchers import UIWatcherManager

logger = logging.getLogger("ui_automation.whatsapp")

# Paquetes soportados (WhatsApp Estándar y WhatsApp Business)
WHATSAPP_PACKAGES = [
    "com.whatsapp",
    "com.whatsapp.w4b",
]

# Indicadores de la pantalla principal de chats (Español / Inglés)
# Pestañas y elementos representativos del ActionBar / BottomNavigation de WhatsApp
CHAT_LIST_TEXTS = [
    "Chats",
    "CHATS",
    "Conversaciones",
]

CHAT_LIST_DESCRIPTIONS = [
    "Chats",
    "Buscar",
    "Search",
    "Más opciones",
    "More options",
    "Nuevo chat",
    "New chat",
    "Cámara",
    "Camera",
]

CHAT_LIST_RESOURCE_IDS = [
    "com.whatsapp:id/tab",
    "com.whatsapp.w4b:id/tab",
    "com.whatsapp:id/fab",
    "com.whatsapp.w4b:id/fab",
    "com.whatsapp:id/menuitem_search",
    "com.whatsapp.w4b:id/menuitem_search",
    "com.whatsapp:id/conversations_row_contact_name",
    "com.whatsapp.w4b:id/conversations_row_contact_name",
]


class WhatsAppClient:
    """Gestiona la apertura, navegación y detección de estados en WhatsApp."""

    def __init__(
        self,
        device_or_manager: Union[DeviceManager, u2.Device],
        preferred_package: Optional[str] = None,
        auto_watchers: bool = True,
    ):
        """
        Inicializa el cliente de WhatsApp.

        :param device_or_manager: Instancia de DeviceManager o uiautomator2.Device.
        :param preferred_package: Paquete específico ('com.whatsapp' o 'com.whatsapp.w4b').
                                  Si es None, se autodetecta el instalado en el teléfono.
        :param auto_watchers: Si es True, activa el UIWatcherManager para evadir popups inesperados.
        """
        if isinstance(device_or_manager, DeviceManager):
            self.device_mgr = device_or_manager
            self._d: Optional[u2.Device] = None
        else:
            self.device_mgr = None
            self._d = device_or_manager

        self.preferred_package = preferred_package
        self.active_package: Optional[str] = None
        self.auto_watchers = auto_watchers
        self.watcher_mgr: Optional[UIWatcherManager] = None

    @property
    def d(self) -> u2.Device:
        """Obtiene la instancia activa de uiautomator2.Device."""
        if self._d is None and self.device_mgr is not None:
            self._d = self.device_mgr.get_device()
        if self._d is None:
            raise RuntimeError("No se pudo obtener una instancia válida de uiautomator2.Device.")
        return self._d

    def detect_installed_package(self) -> Optional[str]:
        """Detecta cuál de las aplicaciones de WhatsApp está instalada en el dispositivo."""
        if self.preferred_package:
            return self.preferred_package

        try:
            installed = self.d.app_list()
            for pkg in WHATSAPP_PACKAGES:
                if pkg in installed:
                    logger.info("Detectado paquete instalado de WhatsApp: %s", pkg)
                    return pkg
        except Exception as exc:
            logger.debug("Error listando aplicaciones con app_list: %s", exc)

        # Fallback usando pm path
        for pkg in WHATSAPP_PACKAGES:
            res = self.d.shell(f"pm path {pkg}").output or ""
            if "package:" in res:
                logger.info("Detectado paquete vía pm path: %s", pkg)
                return pkg

        logger.warning("No se detectó com.whatsapp ni com.whatsapp.w4b en el dispositivo.")
        return None

    def is_on_chat_list(self) -> bool:
        """
        Determina si la vista actual corresponde a la lista principal de chats.
        Verifica selectores tolerantes a cambios de idioma (ES / EN) e IDs de layout.
        """
        try:
            # 1. Comprobar por pestaña o texto "Chats"
            for text in CHAT_LIST_TEXTS:
                if self.d(text=text).exists:
                    return True

            # 2. Comprobar por descripción de contenido (Botón nuevo chat, cámara, búsqueda)
            for desc in ["Nuevo chat", "New chat", "Buscar", "Search"]:
                if self.d(description=desc).exists:
                    return True

            # 3. Comprobar por resourceId conocidos
            for res_id in CHAT_LIST_RESOURCE_IDS:
                if self.d(resourceId=res_id).exists:
                    return True

            # 4. Comprobación por XPath genérico en barra de pestañas o botones de acción
            xpath_query = (
                "//*[contains(@text, 'Chats') or contains(@content-desc, 'Chats') "
                "or contains(@content-desc, 'Nuevo chat') or contains(@content-desc, 'New chat')]"
            )
            if self.d.xpath(xpath_query).exists:
                return True

        except Exception as exc:
            logger.debug("Error evaluando is_on_chat_list: %s", exc)

        return False

    def open_whatsapp(self, timeout: float = 12.0) -> bool:
        """
        Abre WhatsApp (o WhatsApp Business), activa observadores de popups y espera
        a que la lista principal de chats esté completamente cargada.

        :param timeout: Tiempo máximo en segundos para confirmar la carga.
        :return: True si logró ubicarse en la lista de chats, False en caso contrario.
        """
        pkg = self.detect_installed_package()
        if not pkg:
            logger.error("No se puede abrir WhatsApp: la aplicación no está instalada.")
            return False

        self.active_package = pkg
        logger.info("Iniciando WhatsApp (%s)...", pkg)

        # Iniciar watchers para resolver posibles diálogos (ej: permisos, backups, alertas)
        if self.auto_watchers:
            if self.watcher_mgr is None:
                self.watcher_mgr = UIWatcherManager(self.d)
                self.watcher_mgr.setup_auto_popup()
            if not self.watcher_mgr.is_running():
                self.watcher_mgr.start(interval=1.5)

        # Lanzar la app
        try:
            self.d.app_start(pkg, stop=False)
        except Exception as exc:
            logger.error("Fallo al ejecutar app_start(%s): %s", pkg, exc)
            return False

        # Esperar hasta llegar a la lista de chats
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_on_chat_list():
                logger.info("WhatsApp abierto exitosamente en la pantalla principal de chats.")
                return True

            # Si está dentro de una conversación previa, intentar volver a la lista
            if self._is_inside_conversation():
                logger.info("WhatsApp se abrió dentro de un chat. Navegando hacia la lista principal...")
                self.go_back_to_chat_list()
                if self.is_on_chat_list():
                    return True

            time.sleep(0.8)

        # Si expiró el tiempo, revisar si al menos la app está en primer plano
        current_app = self.d.app_current()
        logger.warning(
            "Tiempo de espera agotado buscando la lista de chats. App en primer plano: %s",
            current_app.get("package") if current_app else "desconocida",
        )
        return self.is_on_chat_list()

    def _is_inside_conversation(self) -> bool:
        """Detecta si la pantalla actual es una conversación abierta."""
        # Botones típicos de un chat: botón adjuntar, enviar, campo de texto 'Mensaje'
        conversation_indicators = [
            "com.whatsapp:id/entry",
            "com.whatsapp.w4b:id/entry",
            "com.whatsapp:id/send",
            "com.whatsapp.w4b:id/send",
            "com.whatsapp:id/voice_note_btn",
            "com.whatsapp.w4b:id/voice_note_btn",
        ]
        for res_id in conversation_indicators:
            if self.d(resourceId=res_id).exists:
                return True

        if self.d(text="Mensaje").exists or self.d(text="Message").exists:
            return True

        return False

    def go_back_to_chat_list(self, max_attempts: int = 4) -> bool:
        """
        Navega hacia atrás hasta regresar a la pantalla principal de chats.
        
        :param max_attempts: Número máximo de retrocesos antes de desistir.
        :return: True si terminó en la lista de chats.
        """
        logger.info("Regresando a la lista de chats...")
        for attempt in range(1, max_attempts + 1):
            if self.is_on_chat_list():
                return True

            # 1. Intentar hacer clic en la flecha de regreso de la barra superior (Up button)
            back_btn = self.d(description="Navegar hacia arriba")
            if not back_btn.exists:
                back_btn = self.d(description="Navigate up")
            if not back_btn.exists:
                back_btn = self.d(resourceIdMatches=r".*id/back.*|.*id/up.*")

            if back_btn.exists:
                try:
                    back_btn.click()
                    time.sleep(0.6)
                    if self.is_on_chat_list():
                        return True
                except Exception:
                    pass

            # 2. Fallback con tecla física/ADB Back
            try:
                self.d.press("back")
                time.sleep(0.6)
            except Exception as exc:
                logger.debug("Error presionando tecla back: %s", exc)

            if self.is_on_chat_list():
                logger.info("Se alcanzó la lista de chats tras %d intento(s).", attempt)
                return True

        return self.is_on_chat_list()

    def close(self) -> None:
        """Detiene los observadores asociados si estaban activos."""
        if self.watcher_mgr:
            self.watcher_mgr.stop()
            self.watcher_mgr = None

    # =========================================================================
    # Métodos de Exportación de Chats
    # =========================================================================

    def export_chat(
        self,
        chat_name: str,
        include_media: bool = False,
        timeout: float = 30.0,
    ) -> Optional[str]:
        """
        Exporta el chat indicado usando la funcionalidad nativa de WhatsApp.
        Devuelve la ruta remota del archivo generado en el teléfono o None.
        """
        from .export import WhatsAppExporter
        exporter = WhatsAppExporter(self.d, watcher_mgr=self.watcher_mgr)
        return exporter.export_chat(chat_name=chat_name, include_media=include_media, timeout=timeout)

    def export_chat_structured(
        self,
        chat_name: str,
        include_media: bool = False,
        timeout: float = 30.0,
    ) -> Tuple[Optional[str], Any]:
        """Exporta el chat devolviendo la ruta remota y el ShareSheetResult estructurado."""
        from .export import WhatsAppExporter
        exporter = WhatsAppExporter(self.d, watcher_mgr=self.watcher_mgr)
        return exporter.export_chat_structured(chat_name=chat_name, include_media=include_media, timeout=timeout)

    def find_exported_chat_file(
        self,
        chat_name: Optional[str] = None,
        min_mtime: Optional[int] = None,
        prefer_zip: bool = False,
    ) -> Optional[str]:
        """Busca en el dispositivo el archivo exportado más reciente."""
        from .export import WhatsAppExporter
        exporter = WhatsAppExporter(self.d, watcher_mgr=self.watcher_mgr)
        return exporter.find_exported_chat_file(chat_name=chat_name, min_mtime=min_mtime, prefer_zip=prefer_zip)

    def pull_exported_chat(
        self,
        remote_path: str,
        local_dir: Union[str, Path] = "./exports",
        expected_size_bytes: Optional[int] = None,
        expected_sha256: Optional[str] = None,
    ) -> Path:
        """Descarga el archivo exportado desde el teléfono a la computadora local vía ADB."""
        from .export import WhatsAppExporter
        exporter = WhatsAppExporter(self.d, watcher_mgr=self.watcher_mgr)
        return exporter.pull_exported_chat(
            remote_path=remote_path,
            local_dir=local_dir,
            expected_size_bytes=expected_size_bytes,
            expected_sha256=expected_sha256,
        )

    def delete_exported_artifacts(self, remote_path: str, receipt_path: Optional[str] = None) -> bool:
        """Elimina el archivo y su comprobante tras una descarga validada."""
        from .export import WhatsAppExporter
        exporter = WhatsAppExporter(self.d, watcher_mgr=self.watcher_mgr)
        return exporter.delete_exported_artifacts(remote_path, receipt_path)

    def delete_exported_file(self, remote_path: str) -> bool:
        """Elimina el archivo temporal generado en el teléfono."""
        from .export import WhatsAppExporter
        exporter = WhatsAppExporter(self.d, watcher_mgr=self.watcher_mgr)
        return exporter.delete_exported_file(remote_path=remote_path)

    # =========================================================================
    # Métodos de Procesamiento de Múltiples Chats
    # =========================================================================

    def get_top_chat_names(self, limit: int = 10) -> List[str]:
        """
        Extrae los primeros N nombres de chats más recientes de la lista.
        Realiza scroll controlado si es necesario.
        """
        from .chats import WhatsAppChatManager
        manager = WhatsAppChatManager(self)
        return manager.get_top_chat_names(limit=limit)

    def export_top_chats(
        self,
        n: int = 10,
        include_media: bool = False,
        local_dir: Union[str, Path] = "./exports",
    ) -> List[Dict[str, Any]]:
        """
        Exporta y descarga los primeros N chats más recientes de WhatsApp.
        """
        from .chats import WhatsAppChatManager
        manager = WhatsAppChatManager(self)
        return manager.export_top_chats(n=n, include_media=include_media, local_dir=local_dir)


