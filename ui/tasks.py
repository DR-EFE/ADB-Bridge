"""Utilidad para ejecutar tareas largas en un hilo secundario sin congelar la UI."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("ui.tasks")


class BackgroundTask:
    """Ejecuta ``target`` en un hilo daemon con ciclo de vida controlado.

    Ejemplo::

        task = BackgroundTask(
            "exportación",
            target=lambda stop: do_work(stop),
            on_done=lambda err: update_ui(err),
        )
        task.start()   # vuelve inmediatamente; la UI sigue respondiendo
        task.stop()    # señal cooperativa (stop_event + on_stop opcional)

    ``on_done`` se invoca SIEMPRE al terminar (con la excepción si falló) desde el hilo
    secundario; dentro de él se puede actualizar controles y llamar ``page.update()``.
    """

    def __init__(
        self,
        name: str,
        target: Callable[[threading.Event], None],
        on_done: Optional[Callable[[Optional[BaseException]], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        self.name = name
        self._target = target
        self._on_done = on_done
        self._on_stop = on_stop
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        """True mientras el hilo siga vivo."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Lanza el hilo. Devuelve False si ya estaba corriendo."""
        if self.is_running:
            logger.warning("La tarea '%s' ya está en ejecución.", self.name)
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"task-{self.name}", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Solicita la detención cooperativa de la tarea."""
        if not self.is_running:
            return
        logger.info("Deteniendo tarea '%s'...", self.name)
        self._stop_event.set()
        if self._on_stop:
            try:
                self._on_stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error en on_stop de '%s': %s", self.name, exc)

    def _run(self) -> None:
        error: Optional[BaseException] = None
        logger.info("Tarea '%s' iniciada.", self.name)
        try:
            self._target(self._stop_event)
        except BaseException as exc:  # noqa: BLE001 - se reporta a la UI
            error = exc
            logger.error("Tarea '%s' terminó con error: %s", self.name, exc)
        finally:
            logger.info("Tarea '%s' finalizada.", self.name)
            if self._on_done:
                try:
                    self._on_done(error)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error en on_done de '%s': %s", self.name, exc)
