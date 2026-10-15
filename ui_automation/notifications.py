"""Módulo de extracción y procesamiento de notificaciones de Android."""

import re
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Dict, Any
import uiautomator2 as u2

logger = logging.getLogger("ui_automation.notifications")


@dataclass
class NotificationItem:
    """Representa una notificación extraída del dispositivo."""
    package: str
    title: str
    text: str
    sub_text: str = ""
    post_time: str = ""
    raw_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convierte la notificación a diccionario serializable en JSON."""
        return asdict(self)


class NotificationExtractor:
    """Extrae notificaciones activas del dispositivo mediante dumpsys y UI hierarchy."""

    def __init__(self, device: u2.Device):
        self.d = device

    def fetch_notifications_via_dumpsys(self) -> List[NotificationItem]:
        """
        Extrae notificaciones activas del sistema usando 'dumpsys notification --noredact'.
        Este método es no intrusivo (no requiere bajar la barra de notificaciones ni alterar la UI).
        """
        notifications: List[NotificationItem] = []
        try:
            output = self.d.shell("dumpsys notification --noredact").output
            if not output:
                return notifications

            # Bloques por registro de notificación (NotificationRecord)
            records = re.split(r"NotificationRecord\(", output)
            now_iso = datetime.now().isoformat()

            for rec in records[1:]:
                # Extraer pkg
                pkg_match = re.search(r"pkg=([a-zA-Z0-9_\.]+)", rec)
                pkg = pkg_match.group(1) if pkg_match else "unknown"

                # Ignorar notificaciones persistentes del sistema comunes no deseadas si es necesario
                if pkg in ("android", "com.android.systemui") and "android.title=" not in rec:
                    continue

                # Extraer título
                title_match = re.search(r"android\.title=String\s*\((.*?)\)", rec)
                if not title_match:
                    title_match = re.search(r"android\.title=(.*?)(?=\n|,)", rec)
                title = title_match.group(1).strip() if title_match else ""

                # Extraer texto principal
                text_match = re.search(r"android\.text=String\s*\((.*?)\)", rec)
                if not text_match:
                    text_match = re.search(r"android\.text=(.*?)(?=\n|,)", rec)
                text = text_match.group(1).strip() if text_match else ""

                # Extraer subtexto (opcional)
                sub_match = re.search(r"android\.subText=String\s*\((.*?)\)", rec)
                sub_text = sub_match.group(1).strip() if sub_match else ""

                # Extraer key
                key_match = re.search(r"key=([^\s\n]+)", rec)
                key = key_match.group(1) if key_match else ""

                if title or text:
                    notifications.append(
                        NotificationItem(
                            package=pkg,
                            title=title,
                            text=text,
                            sub_text=sub_text,
                            post_time=now_iso,
                            raw_key=key,
                        )
                    )

            logger.debug("Extraídas %d notificaciones vía dumpsys", len(notifications))
        except Exception as exc:
            logger.error("Error al extraer notificaciones vía dumpsys: %s", exc)

        return notifications

    def fetch_notifications_via_ui(self) -> List[NotificationItem]:
        """
        Extrae notificaciones desplegando la cortina de notificaciones mediante uiautomator2.
        Útil como fallback cuando dumpsys viene truncado.
        """
        notifications: List[NotificationItem] = []
        try:
            self.d.open_notification()
            self.d.sleep(1.0)
            now_iso = datetime.now().isoformat()

            # Buscar contenedores de notificaciones
            titles = self.d(resourceId="android:id/title")
            texts = self.d(resourceId="android:id/text")
            
            count = max(len(titles), len(texts))
            for i in range(count):
                title_val = titles[i].get_text() if i < len(titles) and titles[i].exists else ""
                text_val = texts[i].get_text() if i < len(texts) and texts[i].exists else ""
                
                if title_val or text_val:
                    notifications.append(
                        NotificationItem(
                            package="ui.notification.shade",
                            title=title_val,
                            text=text_val,
                            post_time=now_iso
                        )
                    )

            # Cerrar panel de notificaciones regresando a la app anterior
            self.d.press("back")
        except Exception as exc:
            logger.error("Error al extraer notificaciones vía UI: %s", exc)

        return notifications

    def get_notifications(self, prefer_dumpsys: bool = True) -> List[NotificationItem]:
        """Obtiene notificaciones activas usando la estrategia preferida."""
        if prefer_dumpsys:
            items = self.fetch_notifications_via_dumpsys()
            if items:
                return items
            # Si dumpsys no devolvió nada, intentamos inspeccionar UI
            return self.fetch_notifications_via_ui()
        return self.fetch_notifications_via_ui()
