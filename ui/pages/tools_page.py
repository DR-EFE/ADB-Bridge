"""Pestaña «Herramientas» (placeholder: la lógica de PowerShell se migrará después)."""

from __future__ import annotations

import flet as ft

from .. import theme

_PLANNED = [
    (ft.Icons.SWAP_HORIZ, "Transferencia de archivos", "Pendiente de migrar desde android_transfer.ps1"),
    (ft.Icons.BACKUP, "Backup avanzado", "Pendiente de migrar desde android_advanced_backup.ps1"),
    (ft.Icons.FOLDER_OPEN, "Explorador de archivos", "Pendiente de migrar desde android_file_explorer.ps1"),
]


class ToolsPage:
    """Contenido de la pestaña. El control a insertar es ``.view``."""

    def __init__(self) -> None:
        tiles = [
            theme.card(
                ft.Row(
                    spacing=14,
                    controls=[
                        ft.Icon(icon, size=28, color=theme.TEXT_MUTED),
                        ft.Column(
                            spacing=2,
                            expand=True,
                            controls=[
                                ft.Text(title, weight=ft.FontWeight.W_600),
                                ft.Text(subtitle, size=12, color=theme.TEXT_MUTED),
                            ],
                        ),
                        ft.Text("Próximamente", size=12, color=theme.WARN),
                    ],
                )
            )
            for icon, title, subtitle in _PLANNED
        ]
        self.view = ft.Column(expand=True, spacing=12, controls=tiles)

    def shutdown(self) -> None:
        """Sin recursos que liberar."""
