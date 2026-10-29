"""Adaptador del monitor de notificaciones para la UI (sin modificar ``ui_automation``)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ui_automation.monitor import NotificationMonitor
from ui_automation.notifications import ClassificationResult, NotificationItem, extract_otp_code


class UiNotificationMonitor(NotificationMonitor):
    """``NotificationMonitor`` que además notifica cada evento importante a la UI.

    Mantiene intacta la persistencia/consola de la clase base y solo añade el callback
    ``on_important`` con un diccionario listo para pintar.
    """

    def __init__(
        self,
        *args: Any,
        on_important: Optional[Callable[[Dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_important = on_important

    def _handle_important(self, item: NotificationItem, result: ClassificationResult) -> None:
        super()._handle_important(item, result)
        if not self._on_important:
            return
        codigo = result.codigo or extract_otp_code(" ".join(filter(None, (item.title, item.text, item.sub_text))))
        try:
            self._on_important(
                {
                    "tipo": result.tipo,
                    "codigo": codigo,
                    "fuente": result.fuente,
                    "titulo": item.title,
                    "texto": item.text,
                    "package": item.package,
                    "timestamp": item.timestamp,
                    "device_serial": self._device_serial,
                    "device_id": self.device_id,
                    "razon": result.razon,
                }
            )
        except Exception:  # noqa: BLE001 - la UI nunca debe romper el monitor
            pass
