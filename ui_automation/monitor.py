"""Módulo orquestador del monitoreo y persistencia de notificaciones."""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Callable

from .device import DeviceManager
from .watchers import UIWatcherManager
from .notifications import NotificationExtractor, NotificationItem

logger = logging.getLogger("ui_automation.monitor")


class NotificationMonitor:
    """Orquesta la conexión, extracción de notificaciones y guardado continuo en JSON."""

    def __init__(
        self,
        serial: Optional[str] = None,
        logs_dir: str = "logs",
        poll_interval: float = 3.0,
        enable_watchers: bool = True,
        on_notification_callback: Optional[Callable[[NotificationItem], None]] = None,
    ):
        self.serial = serial
        self.logs_dir = Path(logs_dir)
        self.poll_interval = max(0.5, poll_interval)
        self.enable_watchers = enable_watchers
        self.on_notification_callback = on_notification_callback

        self.device_mgr = DeviceManager(serial=self.serial)
        self.watcher_mgr: Optional[UIWatcherManager] = None
        self.extractor: Optional[NotificationExtractor] = None

        self._running = False
        self._seen_notification_keys: Set[str] = set()

        # Asegurar creación del directorio de logs
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_filepath(self) -> Path:
        """Genera el nombre de archivo diario para los registros JSON."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return self.logs_dir / f"notifications_{today_str}.json"

    def _append_to_json(self, item: NotificationItem) -> None:
        """Guarda una notificación en formato JSON estructurado."""
        filepath = self._get_log_filepath()
        payload = item.to_dict()

        records = []
        if filepath.exists() and filepath.stat().st_size > 0:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    if not isinstance(records, list):
                        records = []
            except Exception as exc:
                logger.warning("No se pudo leer el archivo existente %s: %s. Creando nuevo.", filepath, exc)
                records = []

        records.append(payload)

        # Escritura atómica o directa en UTF-8
        temp_file = filepath.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        temp_file.replace(filepath)
        logger.info("Notificación guardada en: %s", filepath)

    def start(self) -> None:
        """Inicia el bucle de monitoreo continuo."""
        logger.info("Iniciando NotificationMonitor...")
        d = self.device_mgr.get_device()

        if self.enable_watchers:
            self.watcher_mgr = UIWatcherManager(d)
            self.watcher_mgr.register_common_watchers()
            self.watcher_mgr.start(interval=2.0)

        self.extractor = NotificationExtractor(d)
        self._running = True

        logger.info("Monitoreando notificaciones cada %.1fs (Ctrl+C para salir)...", self.poll_interval)

        try:
            while self._running:
                # Validar estado del dispositivo
                if not self.device_mgr.is_alive():
                    logger.warning("Conexión perdida con el dispositivo. Reintentando...")
                    time.sleep(2.0)
                    try:
                        d = self.device_mgr.connect()
                        self.extractor = NotificationExtractor(d)
                    except Exception as exc:
                        logger.error("Fallo al reconectar: %s", exc)
                        time.sleep(self.poll_interval)
                        continue

                # Extraer notificaciones
                notifications = self.extractor.get_notifications(prefer_dumpsys=True)

                for item in notifications:
                    # Deduplicación por combinación única
                    unique_id = f"{item.package}|{item.title}|{item.text}"
                    if unique_id not in self._seen_notification_keys:
                        self._seen_notification_keys.add(unique_id)
                        logger.info("Nueva notificación detectada: [%s] %s - %s", item.package, item.title, item.text)
                        
                        # Persistir
                        self._append_to_json(item)

                        # Callback para integración (ej. IA o automatizaciones externas)
                        if self.on_notification_callback:
                            try:
                                self.on_notification_callback(item)
                            except Exception as cb_err:
                                logger.error("Error en callback de notificación: %s", cb_err)

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("Interrupción por el usuario recibida.")
        finally:
            self.stop()

    def stop(self) -> None:
        """Detiene el monitor y libera recursos."""
        self._running = False
        if self.watcher_mgr:
            self.watcher_mgr.stop()
            self.watcher_mgr = None
        self.device_mgr.disconnect()
        logger.info("NotificationMonitor detenido.")
