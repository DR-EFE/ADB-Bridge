"""Pestaña «Registro & Salida»: visor de log global estilo consola ADB con filtros y copiado."""

from __future__ import annotations

import logging
from typing import List, Optional

import flet as ft

from .. import theme

LEVEL_MAP = {
    "TODOS": None,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
}

LEVEL_COLORS = {
    logging.DEBUG: theme.TEXT_MUTED,
    logging.INFO: theme.TEXT,
    logging.WARNING: theme.WARN,
    logging.ERROR: theme.ERR,
    logging.CRITICAL: theme.ERR,
}


class LogsPage:
    """Pestaña de registro de actividad completa."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._raw_records: List[tuple[int, str]] = []
        self._filter_level: Optional[int] = None
        self._auto_scroll = True

        self._list = ft.ListView(expand=True, auto_scroll=True, spacing=2)

        # Barra de herramientas superior
        self._level_dropdown = ft.Dropdown(
            label="Nivel",
            width=140,
            content_padding=6,
            text_size=12,
            value="TODOS",
            options=[ft.dropdown.Option(k) for k in LEVEL_MAP.keys()],
            on_select=self._on_filter_change,
        )

        self._btn_clear = ft.OutlinedButton(
            "Limpiar",
            icon=ft.Icons.DELETE_SWEEP_OUTLINED,
            on_click=self._on_clear,
        )

        self._btn_copy = ft.OutlinedButton(
            "Copiar Todo",
            icon=ft.Icons.COPY_ALL,
            on_click=self._on_copy,
        )

        self._count_label = ft.Text("0 entradas", size=12, color=theme.TEXT_MUTED)

        self._log_container = ft.Container(
            content=self._list,
            expand=True,
            padding=12,
            bgcolor=theme.BG,
            border_radius=10,
            border=ft.Border.all(1, theme.BORDER),
        )

        self.view = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                theme.card(
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Icon(ft.Icons.TERMINAL, color=theme.ACCENT, size=22),
                                    ft.Text("Registro de Actividad ADB & Sistema", weight=ft.FontWeight.BOLD, size=15),
                                    self._level_dropdown,
                                ],
                            ),
                            ft.Row(
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    self._count_label,
                                    self._btn_clear,
                                    self._btn_copy,
                                ],
                            ),
                        ],
                    ),
                    padding=12,
                ),
                self._log_container,
            ],
        )

    def append_log(self, level: int, message: str) -> None:
        """Agrega un registro nuevo al visor."""
        self._raw_records.append((level, message))
        if len(self._raw_records) > 2000:
            del self._raw_records[: len(self._raw_records) - 2000]

        if self._filter_level is not None and level < self._filter_level:
            return

        color = LEVEL_COLORS.get(level, theme.TEXT)
        self._list.controls.append(
            ft.Text(
                message,
                size=12,
                color=color,
                font_family=theme.MONO_FONT,
                selectable=True,
            )
        )
        if len(self._list.controls) > 1000:
            del self._list.controls[: len(self._list.controls) - 1000]

        self._count_label.value = f"{len(self._raw_records)} entradas"
        try:
            self._list.update()
            self._count_label.update()
        except Exception:
            pass

    def _on_filter_change(self, _e: ft.ControlEvent) -> None:
        self._filter_level = LEVEL_MAP.get(self._level_dropdown.value or "TODOS")
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        self._list.controls.clear()
        for level, msg in self._raw_records:
            if self._filter_level is None or level >= self._filter_level:
                color = LEVEL_COLORS.get(level, theme.TEXT)
                self._list.controls.append(
                    ft.Text(msg, size=12, color=color, font_family=theme.MONO_FONT, selectable=True)
                )
        try:
            self._list.update()
        except Exception:
            pass

    def _on_clear(self, _e: ft.ControlEvent) -> None:
        self._raw_records.clear()
        self._list.controls.clear()
        self._count_label.value = "0 entradas"
        try:
            self._list.update()
            self._count_label.update()
        except Exception:
            pass

    def _on_copy(self, _e: ft.ControlEvent) -> None:
        full_text = "\n".join(msg for _, msg in self._raw_records)
        self.page.set_clipboard(full_text)
        self._count_label.value = "¡Copiado al portapapeles!"
        try:
            self._count_label.update()
        except Exception:
            pass

    def shutdown(self) -> None:
        pass
