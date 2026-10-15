"""Módulo de observadores (watchers) automáticos para popups y diálogos del sistema."""

import logging
from typing import List, Callable, Optional
import uiautomator2 as u2

logger = logging.getLogger("ui_automation.watchers")


class UIWatcherManager:
    """Gestiona observadores de uiautomator2 para interceptar y resolver diálogos inesperados."""

    def __init__(self, device: u2.Device):
        self.d = device
        self._registered_names: List[str] = []

    def register_common_watchers(self) -> None:
        """
        Registra observadores comunes del sistema Android:
        - Diálogos de permisos del sistema (Permitir, Allow, Continuar).
        - Alertas ANR ('La app no responde' -> Esperar).
        - Alertas de cierre inesperado o confirmaciones comunes.
        """
        logger.info("Registrando observadores del sistema comunes...")

        # 1. Permisos del sistema Android
        self.d.watcher("ALLOW_PERMISSION").when("Permitir").click()
        self.d.watcher("ALLOW_PERMISSION_EN").when("Allow").click()
        self.d.watcher("WHILE_USING_APP").when("Mientras la app está en uso").click()
        self.d.watcher("WHILE_USING_APP_EN").when("While using the app").click()

        # 2. Diálogos de advertencia o confirmación frecuentes
        self.d.watcher("SYSTEM_OK").when("Aceptar").click()
        self.d.watcher("SYSTEM_OK_EN").when("OK").click()

        # 3. ANR (App Not Responding) -> Intentar esperar antes de forzar cierre
        self.d.watcher("ANR_WAIT").when("Esperar").click()
        self.d.watcher("ANR_WAIT_EN").when("Wait").click()

        self._registered_names.extend([
            "ALLOW_PERMISSION", "ALLOW_PERMISSION_EN",
            "WHILE_USING_APP", "WHILE_USING_APP_EN",
            "SYSTEM_OK", "SYSTEM_OK_EN",
            "ANR_WAIT", "ANR_WAIT_EN"
        ])
        logger.info("Observadores registrados: %s", self._registered_names)

    def register_custom_watcher(self, name: str, target_text: str, action: Optional[Callable] = None) -> None:
        """
        Registra un observador personalizado para un texto específico.

        :param name: Nombre identificador único.
        :param target_text: Texto que activa el watcher.
        :param action: Función a ejecutar cuando aparece. Si es None, hace click en el elemento.
        """
        rule = self.d.watcher(name).when(target_text)
        if action:
            rule.call(action)
        else:
            rule.click()
        self._registered_names.append(name)
        logger.info("Observador personalizado registrado: %s (detecta '%s')", name, target_text)

    def start(self, interval: float = 2.0) -> None:
        """Inicia el monitoreo de watchers en un hilo en segundo plano."""
        logger.info("Iniciando servicio de watchers en segundo plano (intervalo=%ss)...", interval)
        self.d.watcher.start(interval=interval)

    def stop(self) -> None:
        """Detiene el servicio de watchers en segundo plano."""
        logger.info("Deteniendo servicio de watchers...")
        self.d.watcher.stop()

    def remove_all(self) -> None:
        """Elimina todos los observadores registrados."""
        self.d.watcher.reset()
        self._registered_names.clear()
        logger.info("Todos los observadores fueron eliminados.")
