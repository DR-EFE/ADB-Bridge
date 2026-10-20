"""Tokens de diseño y configuración del tema oscuro de Android Tools."""

from __future__ import annotations

import flet as ft

# Paleta
BG = "#101010"
SURFACE = "#191919"
SURFACE_ALT = "#222222"
BORDER = "#414141"
ACCENT = "#E61919"
OK = "#9CCB8B"
ERR = "#FF5555"
WARN = "#E6B85C"
TEXT = "#EAEAEA"
TEXT_MUTED = "#A3A3A3"

MONO_FONT = "Cascadia Mono"

# Color por tipo de notificación importante
TYPE_COLORS = {"OTP": OK, "ALERTA": WARN, "SPAM": TEXT_MUTED}


def apply_theme(page: ft.Page, title: str = "Android Tools") -> None:
    """Aplica modo oscuro, tamaño de ventana y colores base a la página."""
    page.title = title
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ACCENT, font_family=MONO_FONT)
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1280
    page.window.height = 820
    page.window.min_width = 960
    page.window.min_height = 640


def card(content: ft.Control, padding: int = 16, expand: bool | int | None = None) -> ft.Container:
    """Contenedor estilo tarjeta reutilizable."""
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=SURFACE,
        border_radius=0,
        border=ft.Border.all(1, BORDER),
        expand=expand,
    )


def module_header(index: str, title: str, description: str) -> ft.Control:
    """Shared editorial header that identifies each functional module."""
    return ft.Container(
        padding=ft.Padding.only(left=18, right=18, top=14, bottom=12),
        border=ft.Border.only(bottom=ft.BorderSide(2, ACCENT)),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.END,
            controls=[
                ft.Column(
                    spacing=3,
                    controls=[
                        ft.Text(f"MODULO / {index}", size=10, color=ACCENT, font_family=MONO_FONT),
                        ft.Text(title.upper(), size=24, weight=ft.FontWeight.W_700, font_family=MONO_FONT),
                        ft.Text(description, size=11, color=TEXT_MUTED),
                    ],
                ),
                ft.Text("ANDROID TOOLS  /  ADB", size=10, color=TEXT_MUTED, font_family=MONO_FONT),
            ],
        ),
    )


def module_view(index: str, title: str, description: str, content: ft.Control) -> ft.Control:
    """Add module identity while preserving the original functional control."""
    return ft.Column(
        expand=True,
        spacing=0,
        controls=[module_header(index, title, description), ft.Container(content, padding=14, expand=True)],
    )
