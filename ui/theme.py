"""Tokens de diseño y configuración del tema oscuro de Android Tools."""

from __future__ import annotations

import flet as ft

# Paleta
BG = "#0E1117"
SURFACE = "#161B22"
SURFACE_ALT = "#1F2630"
BORDER = "#2A3340"
ACCENT = "#4F8CFF"
OK = "#34D399"
ERR = "#F87171"
WARN = "#FBBF24"
TEXT = "#E6EDF3"
TEXT_MUTED = "#8B98A9"

MONO_FONT = "Consolas"

# Color por tipo de notificación importante
TYPE_COLORS = {"OTP": OK, "ALERTA": WARN, "SPAM": TEXT_MUTED}


def apply_theme(page: ft.Page, title: str = "Android Tools") -> None:
    """Aplica modo oscuro, tamaño de ventana y colores base a la página."""
    page.title = title
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ACCENT)
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1180
    page.window.height = 780
    page.window.min_width = 960
    page.window.min_height = 640


def card(content: ft.Control, padding: int = 16, expand: bool | int | None = None) -> ft.Container:
    """Contenedor estilo tarjeta reutilizable."""
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=SURFACE,
        border_radius=12,
        border=ft.Border.all(1, BORDER),
        expand=expand,
    )
