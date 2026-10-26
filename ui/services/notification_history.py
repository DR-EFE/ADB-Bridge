"""Lectura y organización del historial de notificaciones por dispositivo y día."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
UNKNOWN_DEVICE = "Sin identificar (historial anterior)"


def device_key(event: Dict[str, Any]) -> str:
    """Usa el ID físico cuando existe; el serial queda como respaldo histórico."""
    return str(event.get("device_id") or event.get("device_serial") or UNKNOWN_DEVICE)


def event_identity(event: Dict[str, Any]) -> tuple[str, ...]:
    """Identifica la misma notificación en los archivos diario e importante."""
    return (device_key(event),) + tuple(str(event.get(key) or "") for key in (
        "package", "titulo", "texto", "timestamp",
    ))


def notification_content_key(event: Dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(event.get(key) or "") for key in ("package", "titulo", "texto", "timestamp"))


def event_day(event: Dict[str, Any]) -> str:
    value = str(event.get("fecha") or "")
    return value[:10] if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-" else "Sin fecha"


def load_notification_history(directory: Path, legacy_directory: Path | None = None) -> List[Dict[str, Any]]:
    """Carga todos los días disponibles sin atribuir registros antiguos a otro equipo."""
    files: list[Path] = []
    for folder in (directory, legacy_directory):
        if folder is None or not folder.is_dir():
            continue
        files.extend(folder.glob("notifications_*.json"))
        files.extend(folder.glob("important_*.json"))
        important = folder / "notificaciones_importantes.json"
        if important.exists():
            files.append(important)

    daily: list[Dict[str, Any]] = []
    important_events: list[Dict[str, Any]] = []
    seen_records: set[tuple[str, ...]] = set()
    for path in sorted(files):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                is_important = path.name.startswith("important_") or path.name == "notificaciones_importantes.json"
                notification = record.get("notification") if is_important else record
                if not isinstance(notification, dict):
                    continue
                classification = record.get("classification") or {}
                if not isinstance(classification, dict):
                    classification = {}
                timestamp = str(notification.get("timestamp") or "")
                event = {
                    "fecha": str((timestamp or record.get("detected_at")) if is_important else timestamp),
                    "timestamp": timestamp,
                    "device_serial": str(record.get("device_serial") or UNKNOWN_DEVICE),
                    "device_id": str(record.get("device_id") or ""),
                    "tipo": str((record.get("tipo") or classification.get("tipo") or "IMPORTANTE") if is_important else (record.get("tipo") or "NORMAL")),
                    "codigo": str((record.get("codigo") or classification.get("codigo") or "") if is_important else (record.get("codigo") or "")),
                    "fuente": str((record.get("fuente") or classification.get("fuente") or "") if is_important else (record.get("fuente") or "diario")),
                    "titulo": str(notification.get("title") or ""),
                    "texto": str(notification.get("text") or ""),
                    "package": str(notification.get("package") or ""),
                }
                identity = (*event_identity(event), event["tipo"], event["fecha"])
                if identity in seen_records:
                    continue
                seen_records.add(identity)
                (important_events if is_important else daily).append(event)
        except (OSError, ValueError, UnicodeError) as exc:
            logger.warning("No se pudo leer el historial %s: %s", path, exc)

    important_keys = {event_identity(event) for event in important_events}
    identified_important = Counter(
        notification_content_key(event) for event in important_events
        if event["device_serial"] != UNKNOWN_DEVICE
    )
    events = important_events + [
        event for event in daily
        if event_identity(event) not in important_keys
        and not (
            event["device_serial"] == UNKNOWN_DEVICE
            and identified_important[notification_content_key(event)] == 1
        )
    ]
    events.sort(key=lambda event: str(event["fecha"]).replace("T", " "), reverse=True)
    return events
