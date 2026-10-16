"""Pestaña «Herramientas» (Obsoleta: Funcionalidad migrada a ExplorerPage y BackupPage)."""

from __future__ import annotations

import flet as ft

from .. import theme
from .backup_page import BackupPage
from .explorer_page import ExplorerPage

__all__ = ["ToolsPage"]


class ToolsPage:
    """Stub de compatibilidad: redirige hacia las vistas completas de Explorador y Backup."""

    def __init__(self) -> None:
        self.view = ft.Container(
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Text("Módulo migrado", weight=ft.FontWeight.BOLD, size=16),
                    ft.Text(
                        "Las herramientas han sido migradas a pestañas independientes:\n"
                        "- 'Explorador & Transferencias': Archivos, carpetas y cola de transferencias.\n"
                        "- 'Backup avanzado': Respaldo multimedia y avanzado sin root con manifiestos.",
                        size=12,
                        color=theme.TEXT_MUTED,
                    ),
                ],
            ),
            padding=16,
        )

    def shutdown(self) -> None:
        pass
