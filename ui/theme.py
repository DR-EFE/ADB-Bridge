"""Tokens de diseño y configuración del tema claro y descansado de Android Tools."""

from __future__ import annotations

import flet as ft

# Paleta Clara (Soft & Clean Light Palette - No invasiva)
BG = "#F8FAFC"            # Fondo principal suave (slate 50)
SURFACE = "#FFFFFF"       # Tarjetas y paneles principales (blanco puro)
SURFACE_ALT = "#F1F5F9"   # Superficies secundarias y fondos alternos (slate 100)
BORDER = "#E2E8F0"        # Bordes tenues y sutiles (slate 200)

ACCENT = "#2563EB"        # Azul profesional limpio (Tailwind blue-600)
ACCENT_LIGHT = "#EFF6FF"  # Azul muy tenue para selecciones y badges (blue-50)

OK = "#059669"            # Verde esmeralda para conexiones exitosas (emerald-600)
OK_LIGHT = "#ECFDF5"      # Fondo verde suave para estados activos (emerald-50)

ERR = "#DC2626"           # Rojo suave para errores (red-600)
ERR_LIGHT = "#FEF2F2"     # Fondo rojo suave (red-50)

WARN = "#D97706"          # Ámbar suave para advertencias (amber-600)
WARN_LIGHT = "#FFFBEB"    # Fondo ámbar suave (amber-50)

TEXT = "#0F172A"          # Texto principal oscuro de alta legibilidad (slate 900)
TEXT_MUTED = "#64748B"    # Texto secundario/descriptivo suave (slate 500)

CARD_BG = SURFACE
MONO_FONT = "Consolas, monospace"

# Color por tipo de notificación importante
TYPE_COLORS = {"OTP": OK, "ALERTA": WARN, "SPAM": TEXT_MUTED}


def apply_theme(page: ft.Page, title: str = "Android Tools") -> None:
    """Aplica tema claro limpio, legible y descansado a la vista."""
    page.title = title
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(
        color_scheme_seed=ACCENT,
        font_family="Segoe UI, Inter, Roboto, Arial, sans-serif",
    )
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1280
    page.window.height = 820
    page.window.min_width = 960
    page.window.min_height = 640


def card(content: ft.Control, padding: int = 14, expand: bool | int | None = None) -> ft.Container:
    """Contenedor estilo tarjeta limpio con bordes suaves redondeados."""
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=SURFACE,
        border_radius=10,
        border=ft.Border.all(1, BORDER),
        expand=expand,
    )


def module_header(index: str, title: str, description: str) -> ft.Control:
    """Cabecera sutil y no invasiva para módulos."""
    return ft.Container(
        padding=ft.Padding.only(left=14, right=14, top=8, bottom=8),
        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=8,
                    controls=[
                        ft.Container(
                            content=ft.Text(f"M{index}", size=10, weight=ft.FontWeight.BOLD, color=ACCENT),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            bgcolor=ACCENT_LIGHT,
                            border_radius=4,
                        ),
                        ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=TEXT),
                    ],
                ),
                ft.Text(description, size=11, color=TEXT_MUTED),
            ],
        ),
    )


def module_view(index: str, title: str, description: str, content: ft.Control) -> ft.Control:
    """Contenedor de módulo limpio y amplio sin cabeceras redundantes ni líneas invasivas."""
    return ft.Container(
        content=content,
        padding=12,
        expand=True,
    )
