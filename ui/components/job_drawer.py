"""Componente de panel inferior colapsable/expandible para la Cola de Operaciones (JobDrawer).

Características:
- Barra colapsada siempre visible en la parte inferior de la ventana.
- Spinner animado cuando hay trabajos activos (PENDING o RUNNING).
- Resumen en tiempo real ("X en ejecución | Y en cola | Último: Z").
- Badge con el conteo total de trabajos pendientes + ejecutándose.
- Panel expandido con scroll, mostrando tarjetas de cada trabajo con:
  - Título y tipo de operación.
  - Serial del dispositivo o etiqueta 'Local'.
  - Barra de progreso porcentual y texto de etapa actual.
  - Indicador de estado con color (Verde, Azul, Amarillo, Rojo, Gris).
  - Tiempo transcurrido / fecha de creación.
  - Botón Cancelar (para trabajos activos o en cola).
  - Botón Reintentar (para trabajos fallidos o cancelados).
- Botón global para "Limpiar completados".
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

import flet as ft

from .. import theme
from ..services.job_queue_service import JobItem, JobQueueService, JobStatus, JobType

logger = logging.getLogger("ui.job_drawer")


class JobDrawer:
    """Panel transversal inferior para la supervisión y control de la cola de trabajos."""

    def __init__(
        self,
        queue_service: JobQueueService,
        on_badge_update: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._service = queue_service
        self._on_badge_update = on_badge_update
        self._is_expanded = False

        # ---------------------------------------------------------------------
        # Controles de la Barra Colapsada
        # ---------------------------------------------------------------------
        self._spinner = ft.ProgressRing(width=16, height=16, stroke_width=2.5, color=theme.ACCENT, visible=False)
        self._idle_icon = ft.Icon(ft.Icons.TASK_ALT, size=18, color=theme.TEXT_MUTED)
        self._summary_text = ft.Text("Cola de trabajos inactiva", size=12, color=theme.TEXT)
        self._badge_text = ft.Text("0", size=11, weight=ft.FontWeight.BOLD, color=theme.BG)
        self._badge_container = ft.Container(
            content=self._badge_text,
            bgcolor=theme.ACCENT,
            padding=ft.Padding.symmetric(horizontal=7, vertical=2),
            border_radius=10,
            visible=False,
        )
        self._toggle_btn = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_UP,
            tooltip="Expandir cola de trabajos",
            icon_color=theme.TEXT_MUTED,
            on_click=self._on_toggle_clicked,
        )

        self._collapsed_bar = ft.Container(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._spinner,
                            self._idle_icon,
                            self._badge_container,
                            self._summary_text,
                        ],
                    ),
                    ft.Row(
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.TextButton(
                                "Limpiar completados",
                                icon=ft.Icons.CLEAR_ALL,
                                on_click=self._on_clear_completed_clicked,
                            ),
                            self._toggle_btn,
                        ],
                    ),
                ],
            ),
            bgcolor=theme.SURFACE_ALT,
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            border=ft.Border.only(top=ft.BorderSide(1, theme.BORDER)),
            height=46,
        )

        # ---------------------------------------------------------------------
        # Controles del Panel Expandido
        # ---------------------------------------------------------------------
        self._jobs_list_view = ft.ListView(expand=True, spacing=8, padding=12)
        self._expanded_container = ft.Container(
            content=self._jobs_list_view,
            bgcolor=theme.SURFACE,
            height=260,
            visible=False,
            border=ft.Border.only(top=ft.BorderSide(1, theme.BORDER)),
        )

        # Vista principal ensamblada
        self.view = ft.Column(
            spacing=0,
            controls=[
                self._expanded_container,
                self._collapsed_bar,
            ],
        )

        # Suscribirse a cambios en el servicio
        self._service.add_listener(self._on_job_updated)
        # Renderizado inicial
        self._refresh_ui()

    # -------------------------------------------------------------------------
    # Manejo de Eventos y Renderizado
    # -------------------------------------------------------------------------

    def _on_toggle_clicked(self, _e: ft.ControlEvent) -> None:
        self._is_expanded = not self._is_expanded
        self._expanded_container.visible = self._is_expanded
        self._toggle_btn.icon = ft.Icons.KEYBOARD_ARROW_DOWN if self._is_expanded else ft.Icons.KEYBOARD_ARROW_UP
        self._toggle_btn.tooltip = "Colapsar cola de trabajos" if self._is_expanded else "Expandir cola de trabajos"
        self._refresh_ui()
        try:
            self.view.update()
        except Exception:
            pass

    def _on_clear_completed_clicked(self, _e: ft.ControlEvent) -> None:
        self._service.clear_completed()
        self._refresh_ui()

    def _on_job_updated(self, _job: JobItem) -> None:
        """Invocado desde hilos de trabajo cuando el estado de un job cambia."""
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        """Actualiza el resumen de la barra colapsada y la lista de tarjetas si está expandida."""
        jobs = self._service.get_history(limit=50)
        running = sum(1 for j in jobs if j.status == JobStatus.RUNNING)
        pending = sum(1 for j in jobs if j.status == JobStatus.PENDING)
        active_total = running + pending

        # Actualizar spinner e icono
        self._spinner.visible = (active_total > 0)
        self._idle_icon.visible = (active_total == 0)

        # Actualizar Badge
        if active_total > 0:
            self._badge_text.value = str(active_total)
            self._badge_container.visible = True
        else:
            self._badge_container.visible = False

        if self._on_badge_update:
            try:
                self._on_badge_update(active_total)
            except Exception:
                pass

        # Texto de resumen
        if active_total > 0:
            parts = []
            if running > 0:
                parts.append(f"{running} en ejecución")
            if pending > 0:
                parts.append(f"{pending} en cola")
            summary = " | ".join(parts)
        else:
            # Mostrar último completado si existe
            last_finished = next(
                (j for j in jobs if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)),
                None,
            )
            if last_finished:
                status_label = "OK" if last_finished.status == JobStatus.COMPLETED else last_finished.status.value.upper()
                summary = f"Último trabajo: {last_finished.title} ({status_label})"
            else:
                summary = "Cola de trabajos inactiva (sin tareas recientes)"

        self._summary_text.value = summary

        # Si está expandido, refrescar tarjetas
        if self._is_expanded:
            self._render_cards(jobs)

        try:
            self.view.update()
        except Exception:
            pass

    def _render_cards(self, jobs: list[JobItem]) -> None:
        self._jobs_list_view.controls.clear()

        if not jobs:
            self._jobs_list_view.controls.append(
                ft.Container(
                    content=ft.Text("No hay trabajos registrados en el historial.", color=theme.TEXT_MUTED, size=12),
                    alignment=ft.Alignment.CENTER,
                    padding=20,
                )
            )
            return

        for job in jobs:
            # Color por estado
            status_color = theme.ACCENT
            status_text = "EN EJECUCIÓN"
            if job.status == JobStatus.PENDING:
                status_color = theme.WARN
                status_text = "EN COLA"
            elif job.status == JobStatus.COMPLETED:
                status_color = theme.OK
                status_text = "COMPLETADO"
            elif job.status == JobStatus.FAILED:
                status_color = theme.ERR
                status_text = "FALLIDO"
            elif job.status == JobStatus.CANCELLED:
                status_color = theme.TEXT_MUTED
                status_text = "CANCELADO"

            # Icono por tipo
            icon = ft.Icons.SETTINGS_OUTLINED
            if job.job_type == JobType.WHATSAPP_EXPORT_CHAT:
                icon = ft.Icons.CHAT_OUTLINED
            elif job.job_type == JobType.WHATSAPP_PROCESS:
                icon = ft.Icons.ANALYTICS_OUTLINED
            elif job.job_type == JobType.BACKUP_CATEGORY:
                icon = ft.Icons.BACKUP_OUTLINED
            elif job.job_type == JobType.FILE_TRANSFER:
                icon = ft.Icons.SWAP_HORIZ
            elif job.job_type == JobType.ADB_COMMAND:
                icon = ft.Icons.TERMINAL

            # Botones de acción
            action_buttons = []
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                action_buttons.append(
                    ft.TextButton(
                        "Cancelar",
                        icon=ft.Icons.CANCEL_OUTLINED,
                        on_click=lambda _, jid=job.id: self._service.cancel(jid),
                    )
                )
            elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                action_buttons.append(
                    ft.TextButton(
                        "Reintentar",
                        icon=ft.Icons.REFRESH,
                        on_click=lambda _, jid=job.id: self._service.retry(jid),
                    )
                )

            # Tarjeta de trabajo
            card = theme.card(
                ft.Column(
                    spacing=6,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Icon(icon, size=18, color=theme.ACCENT),
                                        ft.Text(job.title, weight=ft.FontWeight.W_600, size=13),
                                        ft.Container(
                                            content=ft.Text(job.serial or "LOCAL", size=10, weight=ft.FontWeight.BOLD),
                                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                            bgcolor=theme.SURFACE_ALT,
                                            border_radius=4,
                                        ),
                                    ],
                                ),
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Container(
                                            content=ft.Text(status_text, size=10, weight=ft.FontWeight.BOLD, color=status_color),
                                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                            border=ft.Border.all(1, status_color),
                                            border_radius=4,
                                        ),
                                        *action_buttons,
                                    ],
                                ),
                            ],
                        ),
                        # Barra de progreso si está activo o completado
                        ft.ProgressBar(
                            value=job.progress_percent,
                            color=status_color,
                            bgcolor=theme.BORDER,
                            height=4,
                        ),
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(
                                    job.current_step or (job.error if job.error else "Listo"),
                                    size=11,
                                    color=theme.ERR if job.error else theme.TEXT_MUTED,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    max_lines=1,
                                    expand=True,
                                ),
                                ft.Text(
                                    f"{int(job.progress_percent * 100)}%",
                                    size=11,
                                    color=theme.TEXT_MUTED,
                                ),
                            ],
                        ),
                    ],
                ),
                padding=10,
            )
            self._jobs_list_view.controls.append(card)
