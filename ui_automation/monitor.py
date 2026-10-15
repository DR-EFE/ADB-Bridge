"""Orquestador del monitoreo, clasificación y persistencia de notificaciones.

Flujo por ciclo:
    1. Lee las notificaciones activas (``NotificationExtractor``).
    2. Filtra solo las nuevas (deduplicación por firma).
    3. Clasifica cada nueva con ``classify_notification`` (IA local -> reglas).
    4. Si es importante (OTP / ALERTA): guarda el evento en
       ``logs/notificaciones_importantes.json``, copia ese archivo al teléfono
       y muestra un aviso destacado en consola.

Ningún error de extracción, clasificación o escritura detiene el bucle;
solo Ctrl+C o :meth:`NotificationMonitor.stop` lo finalizan.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .device import DeviceManager
from .notifications import (
    ClassificationResult,
    NotificationExtractor,
    NotificationItem,
    classify_notification,
    extract_otp_code,
)
from .ollama import OllamaServiceManager
from .watchers import UIWatcherManager

logger = logging.getLogger("ui_automation.monitor")

IMPORTANT_TYPES = ("OTP", "ALERTA")
DEFAULT_IMPORTANT_FILENAME = "notificaciones_importantes.json"
DEFAULT_DEVICE_COPY_PATH = "/sdcard/Download/notificaciones_otp.json"
_MAX_SEEN_SIGNATURES = 5000  # Evita que la memoria crezca sin límite en sesiones largas.


# =============================================================================
# Utilidades de E/S resilientes
# =============================================================================

def _safe_print(text: str) -> None:
    """Imprime sin fallar aunque la consola no soporte ciertos caracteres (p. ej. emojis en cp1252)."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"), flush=True)
    except Exception:
        logger.info(text)


def _read_json_list(path: Path) -> List[Any]:
    """Lee un archivo JSON que contiene una lista.

    Si el contenido está corrupto, lo respalda como ``*.corrupt-<fecha>.json``
    antes de empezar una lista nueva (nunca se sobrescriben datos en silencio).
    Los errores de E/S (permisos, bloqueo) se propagan para abortar la escritura.
    """
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        raise ValueError("el contenido no es una lista JSON")
    except ValueError as exc:
        backup = path.with_name(f"{path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        path.replace(backup)
        logger.warning("Archivo JSON inválido (%s). Respaldado como %s.", exc, backup.name)
        return []


def _append_json_record(path: Path, record: Dict[str, Any]) -> bool:
    """Añade un registro a una lista JSON con escritura atómica. Devuelve True si se guardó."""
    try:
        records = _read_json_list(path)
        records.append(record)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        return True
    except Exception as exc:
        logger.error("No se pudo escribir en %s: %s", path, exc)
        return False


# =============================================================================
# Monitor
# =============================================================================

class NotificationMonitor:
    """Monitorea, clasifica y persiste notificaciones de un dispositivo Android."""

    def __init__(
        self,
        serial: Optional[str] = None,
        logs_dir: str = "logs",
        poll_interval: float = 3.0,
        enable_watchers: bool = True,
        on_notification_callback: Optional[Callable[[NotificationItem], None]] = None,
        use_ai: bool = True,
        important_filename: str = DEFAULT_IMPORTANT_FILENAME,
        device_copy_path: Optional[str] = DEFAULT_DEVICE_COPY_PATH,
        save_all_notifications: bool = True,
    ):
        """
        :param serial: Serial USB o ``IP:PUERTO``. None = autodetección.
        :param logs_dir: Carpeta local para los JSON.
        :param poll_interval: Segundos entre lecturas (mínimo 0.5).
        :param enable_watchers: Activa el cierre automático de popups.
        :param on_notification_callback: Se invoca con cada notificación nueva (importante o no).
        :param use_ai: Intentar clasificar con Ollama antes de usar reglas.
        :param important_filename: Nombre del JSON de eventos importantes dentro de ``logs_dir``.
        :param device_copy_path: Ruta en el teléfono para la copia. None desactiva la copia.
        :param save_all_notifications: Guarda también todas las notificaciones en un JSON diario.
        """
        self.serial = serial
        self.logs_dir = Path(logs_dir)
        self.poll_interval = max(0.5, poll_interval)
        self.enable_watchers = enable_watchers
        self.on_notification_callback = on_notification_callback
        self.use_ai = use_ai
        self.important_filename = important_filename
        self.device_copy_path = device_copy_path
        self.save_all_notifications = save_all_notifications

        self.device_mgr = DeviceManager(serial=self.serial)
        self.ollama_mgr = OllamaServiceManager()
        self.watcher_mgr: Optional[UIWatcherManager] = None
        self.extractor: Optional[NotificationExtractor] = None

        self._stop_event = threading.Event()
        self._running = False
        self._cleaned_up = True
        self._device_serial: str = serial or "auto"
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self.stats: Dict[str, int] = {"nuevas": 0, "importantes": 0, "ai": 0, "rules": 0, "copias_telefono": 0}

        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ rutas

    @property
    def important_log_path(self) -> Path:
        """Ruta local del JSON de eventos importantes."""
        return self.logs_dir / self.important_filename

    def _daily_log_path(self) -> Path:
        """Ruta del JSON diario con todas las notificaciones."""
        return self.logs_dir / f"notifications_{datetime.now():%Y-%m-%d}.json"

    # ------------------------------------------------------- deduplicación

    @staticmethod
    def _signature(item: NotificationItem) -> str:
        """Firma única: la key de Android identifica la notificación y el contenido
        detecta actualizaciones (p. ej. un chat que cambia de texto con la misma key)."""
        return f"{item.key or item.package}|{item.title}|{item.text}"

    def _is_new(self, item: NotificationItem) -> bool:
        """Registra la firma y devuelve True solo la primera vez que se ve."""
        signature = self._signature(item)
        if signature in self._seen:
            self._seen.move_to_end(signature)  # Las activas no se expulsan del caché.
            return False
        self._seen[signature] = None
        if len(self._seen) > _MAX_SEEN_SIGNATURES:
            self._seen.popitem(last=False)
        return True

    # ------------------------------------------------------------ procesado

    def _process_new_notification(self, item: NotificationItem) -> None:
        """Clasifica una notificación nueva y ejecuta las acciones correspondientes."""
        self.stats["nuevas"] += 1
        logger.info("Nueva notificación: [%s] %s - %s", item.package, item.title, item.text)

        if self.save_all_notifications:
            _append_json_record(self._daily_log_path(), item.to_dict())

        result = classify_notification(item, use_ai=self.use_ai)  # Nunca lanza excepciones.
        self.stats[result.fuente] = self.stats.get(result.fuente, 0) + 1
        logger.debug("Clasificación: %s", result.to_dict())

        if result.importante and result.tipo in IMPORTANT_TYPES:
            self._handle_important(item, result)

        if self.on_notification_callback:
            try:
                self.on_notification_callback(item)
            except Exception as exc:
                logger.error("Error en on_notification_callback: %s", exc)

    def _handle_important(self, item: NotificationItem, result: ClassificationResult) -> None:
        """Persiste el evento, lo copia al teléfono y lo anuncia en consola."""
        self.stats["importantes"] += 1
        codigo = result.codigo or extract_otp_code(" ".join(filter(None, (item.title, item.text, item.sub_text))))

        event: Dict[str, Any] = {
            "detected_at": datetime.now().isoformat(timespec="seconds"),
            "device_serial": self._device_serial,
            "tipo": result.tipo,
            "codigo": codigo,
            "fuente": result.fuente,
            "notification": item.to_dict(),
            "classification": result.to_dict(),
        }

        saved = _append_json_record(self.important_log_path, event)
        copied = self._copy_to_device() if saved else False
        self._print_alert(item, result, codigo, saved, copied)

    def _copy_to_device(self) -> bool:
        """Sube el JSON de eventos importantes al teléfono. Fallo = aviso, nunca excepción."""
        if not self.device_copy_path:
            return False
        try:
            self.device_mgr.get_device().push(str(self.important_log_path), self.device_copy_path)
            self.stats["copias_telefono"] += 1
            return True
        except Exception as exc:
            logger.warning("No se pudo copiar a %s en el teléfono: %s", self.device_copy_path, exc)
            return False

    def _print_alert(
        self,
        item: NotificationItem,
        result: ClassificationResult,
        codigo: Optional[str],
        saved: bool,
        copied: bool,
    ) -> None:
        """Muestra un bloque destacado en consola (solo ASCII en los bordes)."""
        if result.fuente == "ai":
            fuente = f"ai (modelo {result.modelo}, {result.latencia_ms} ms)"
        else:
            fuente = "rules (reglas clásicas)"
        text = (item.text or "").replace("\n", " ")
        bar = "=" * 66
        lines = [
            "",
            bar,
            f"  >>> NOTIFICACION IMPORTANTE  [{result.tipo}] <<<",
            bar,
            f"  Titulo : {item.title or '(sin título)'}",
            f"  App    : {item.package}",
            f"  Texto  : {text[:140]}{'...' if len(text) > 140 else ''}",
        ]
        if codigo:
            lines.append(f"  CODIGO : {codigo}")
        lines += [
            f"  Fuente : {fuente}",
            f"  Razón  : {result.razon}",
            f"  Guardado: local={'sí' if saved else 'NO'} | teléfono="
            f"{'sí' if copied else ('desactivado' if not self.device_copy_path else 'NO')}",
            bar,
            "",
        ]
        _safe_print("\n".join(lines))

    # ---------------------------------------------------------------- bucle

    def _sleep(self, seconds: float) -> None:
        """Espera en tramos cortos: responde rápido a stop() y a Ctrl+C en Windows."""
        deadline = time.monotonic() + seconds
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))

    def _ensure_connection(self) -> bool:
        """Verifica la sesión y reconecta si se perdió. Devuelve True si hay dispositivo."""
        if self.device_mgr.is_alive():
            return True
        logger.warning("Conexión perdida con el dispositivo. Reintentando...")
        try:
            device = self.device_mgr.connect()
            self.extractor = NotificationExtractor(device)
            return True
        except Exception as exc:
            logger.error("Fallo al reconectar: %s", exc)
            return False

    def _poll_once(self) -> None:
        """Un ciclo de lectura + procesado. Los errores se registran y no detienen el bucle."""
        try:
            if not self._ensure_connection() or self.extractor is None:
                return
            for item in self.extractor.get_notifications(prefer_dumpsys=True):
                if self._stop_event.is_set():
                    break
                if self._is_new(item):
                    try:
                        self._process_new_notification(item)
                    except Exception as exc:
                        logger.exception("Error procesando notificación de %s: %s", item.package, exc)
        except Exception as exc:
            logger.exception("Error en el ciclo de monitoreo: %s", exc)

    def start(self) -> None:
        """Ejecuta el monitoreo hasta Ctrl+C o :meth:`stop`.

        :raises DeviceConnectionError: Si no hay dispositivo al iniciar.
        """
        self._stop_event.clear()
        self._running = True
        self._cleaned_up = False
        try:
            # Si use_ai está activo, verificar o arrancar Ollama local en segundo plano
            if self.use_ai:
                self.ollama_mgr.start_if_needed(wait_seconds=5.0)

            device = self.device_mgr.get_device()
            self._device_serial = getattr(device, "serial", None) or self._device_serial

            if self.enable_watchers:
                self.watcher_mgr = UIWatcherManager(device)
                self.watcher_mgr.setup_auto_popup()
                self.watcher_mgr.start(interval=2.0)

            self.extractor = NotificationExtractor(device)
            logger.info(
                "Monitoreando cada %.1fs | IA=%s | eventos -> %s | copia teléfono -> %s (Ctrl+C para salir)",
                self.poll_interval,
                "activada" if self.use_ai else "desactivada",
                self.important_log_path,
                self.device_copy_path or "desactivada",
            )

            while not self._stop_event.is_set():
                self._poll_once()
                self._sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("Interrupción por el usuario (Ctrl+C).")
        finally:
            self._running = False
            self._cleanup()

    def stop(self) -> None:
        """Solicita la detención. Seguro de llamar desde otro hilo o varias veces."""
        self._stop_event.set()
        if not self._running:
            self._cleanup()

    def _cleanup(self) -> None:
        """Libera watchers, conexión y detiene Ollama si lo iniciamos nosotros."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        if self.watcher_mgr:
            try:
                self.watcher_mgr.stop()
            except Exception as exc:
                logger.debug("Error deteniendo watchers: %s", exc)
            self.watcher_mgr = None
        self.device_mgr.disconnect()

        # Detener Ollama solo si fue iniciado por el monitor
        if self.use_ai:
            self.ollama_mgr.stop_if_started_by_us()

        logger.info(
            "Monitor detenido. Nuevas=%d | Importantes=%d | Clasificadas por IA=%d | por reglas=%d | Copias al teléfono=%d",
            self.stats["nuevas"],
            self.stats["importantes"],
            self.stats.get("ai", 0),
            self.stats.get("rules", 0),
            self.stats["copias_telefono"],
        )
