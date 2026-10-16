"""Visor de logs en vivo y handler de ``logging`` que lo alimenta."""

from __future__ import annotations

import logging
from typing import Callable, Optional, Sequence

import flet as ft

from .. import theme

_LEVEL_COLORS = {
    logging.WARNING: theme.WARN,
    logging.ERROR: theme.ERR,
    logging.CRITICAL: theme.ERR,
}


class LogView:
    """Lista de líneas de log con auto-scroll. El control a insertar es ``.view``."""

    def __init__(self, height: Optional[int] = None, max_lines: int = 400) -> None:
        self._max_lines = max_lines
        self._list = ft.ListView(expand=True, auto_scroll=True, spacing=1)
        self.view = ft.Container(
            content=self._list,
            height=height,
            padding=10,
            bgcolor=theme.BG,
            border_radius=8,
            border=ft.Border.all(1, theme.BORDER),
            expand=height is None,
        )

    def append(self, text: str, color: str = theme.TEXT_MUTED) -> None:
        """Agrega una línea (seguro desde hilos secundarios)."""
        self._list.controls.append(
            ft.Text(text, size=12, color=color, font_family=theme.MONO_FONT, selectable=True)
        )
        if len(self._list.controls) > self._max_lines:
            del self._list.controls[: len(self._list.controls) - self._max_lines]
        try:
            self._list.update()
        except Exception:  # noqa: BLE001 - control aún no montado
            pass

    def clear(self) -> None:
        self._list.controls.clear()
        try:
            self._list.update()
        except Exception:  # noqa: BLE001
            pass


class UiLogHandler(logging.Handler):
    """Reenvía registros de ciertos loggers a un callback ``(nivel, mensaje)``."""

    def __init__(self, callback: Callable[[int, str], None], prefixes: Sequence[str]) -> None:
        super().__init__(level=logging.INFO)
        self._callback = callback
        self._prefixes = tuple(prefixes)
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith(self._prefixes):
            return
        try:
            self._callback(record.levelno, self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


def level_color(level: int) -> str:
    """Color de texto según nivel de log."""
    return _LEVEL_COLORS.get(level, theme.TEXT_MUTED)
