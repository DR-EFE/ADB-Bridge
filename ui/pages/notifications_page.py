"""Pestaña «Monitor de Notificaciones»: encender/apagar, intervalo, IA, filtros y visualización estructurada.

Mantiene las notificaciones completamente separadas del registro técnico de ADB, errores,
watchers y conexión (los cuales se visualizan exclusivamente en LogsPage / 'Registro & Salida').
Persiste y consulta desde la carpeta independiente 'notifications/'.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import flet as ft

from .. import theme
from ..services.device_service import DeviceService
from ..services.monitor_service import UiNotificationMonitor
from ..tasks import BackgroundTask

logger = logging.getLogger("ui.notifications")

MAX_DISPLAY_RECORDS = 300
NOTIFICATIONS_DIR = Path("notifications")
IMPORTANT_FILE = NOTIFICATIONS_DIR / "notificaciones_importantes.json"


class NotificationsPage:
    """Contenido de la pestaña de notificaciones sin logs técnicos mezclados."""

    def __init__(self, device: DeviceService) -> None:
        self._device = device
        self._monitor: Optional[UiNotificationMonitor] = None
        self._task: Optional[BackgroundTask] = None
        self._new_count = 0
        self._important_count = 0

        # Almacén en memoria de eventos para filtros en tiempo real
        self._all_events: List[Dict[str, Any]] = []

        # Controles superiores de encendido / configuración
        self._toggle_btn = ft.FilledButton(
            "Encender",
            icon=ft.Icons.PLAY_ARROW,
            bgcolor=theme.OK,
            color=theme.BG,
            on_click=self._on_toggle,
        )
        self._interval_label = ft.Text("Intervalo: 3 s", size=13)
        self._interval = ft.Slider(
            min=1,
            max=30,
            divisions=29,
            value=3,
            label="{value} s",
            width=220,
            on_change=self._on_interval_change,
        )
        self._ai_switch = ft.Switch(label="IA local (Ollama)", value=True)
        self._state_chip = ft.Text("Detenido", size=12, color=theme.TEXT_MUTED)
        self._stats = ft.Text("Nuevas: 0  |  Importantes: 0", size=12, color=theme.TEXT_MUTED)

        # Botones de archivos de almacenamiento independiente
        self._btn_open_important = ft.OutlinedButton(
            "Historial Importantes",
            icon=ft.Icons.STAR,
            on_click=self._on_open_important_clicked,
        )
        self._btn_open_daily = ft.OutlinedButton(
            "Historial Diario",
            icon=ft.Icons.CALENDAR_TODAY,
            on_click=self._on_open_daily_clicked,
        )
        self._btn_open_folder = ft.OutlinedButton(
            "Carpeta de Notificaciones",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._on_open_folder_clicked,
        )

        # Filtros de visualización
        self._filter_tipo = ft.Dropdown(
            label="Tipo",
            options=[
                ft.dropdown.Option("TODOS", "Todos los tipos"),
                ft.dropdown.Option("OTP", "OTP (Códigos de verificación)"),
                ft.dropdown.Option("ALERTA", "Alertas críticas"),
                ft.dropdown.Option("NORMAL", "Normales / Otras"),
            ],
            value="TODOS",
            width=200,
            text_size=12,
            on_change=self._on_filter_changed,
        )
        self._filter_app = ft.TextField(
            label="Filtrar por app (paquete o nombre)",
            hint_text="ej: whatsapp, banco...",
            width=250,
            text_size=12,
            on_change=self._on_filter_changed,
        )
        self._filter_date = ft.TextField(
            label="Filtrar por fecha",
            hint_text="YYYY-MM-DD",
            width=150,
            text_size=12,
            on_change=self._on_filter_changed,
        )
        self._btn_reload = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip="Recargar notificaciones desde disco",
            on_click=lambda _: self._load_persisted_notifications(),
        )

        # Vista destacada para OTP y ALERTA
        self._featured_panel = ft.Row(
            wrap=True,
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )
        self._featured_container = ft.Column(
            spacing=6,
            controls=[
                ft.Row([
                    ft.Icon(ft.Icons.SHIELD, size=16, color=theme.WARN),
                    ft.Text("Códigos OTP y Alertas Recientes Destacadas", size=13, weight=ft.FontWeight.BOLD),
                ]),
                self._featured_panel,
            ],
            visible=False,
        )

        # Tabla de todas las notificaciones
        self._table_container = ft.Column(
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self._empty_label = ft.Text(
            "No hay notificaciones para mostrar con los filtros actuales.",
            size=13,
            color=theme.TEXT_MUTED,
        )

        # Cargar notificaciones persistidas al iniciar
        self._load_persisted_notifications()

        # Construcción de la vista (¡Sin LogView técnico!)
        self.view = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                # Tarjeta de Control superior
                theme.card(
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                wrap=True,
                                spacing=16,
                                run_spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    self._toggle_btn,
                                    ft.Column([self._interval_label, self._interval], spacing=0),
                                    self._ai_switch,
                                    ft.Column([self._state_chip, self._stats], spacing=2),
                                ],
                            ),
                            ft.Row(
                                wrap=True,
                                spacing=8,
                                controls=[
                                    self._btn_open_important,
                                    self._btn_open_daily,
                                    self._btn_open_folder,
                                ],
                            ),
                        ],
                    ),
                    padding=12,
                ),
                # Panel destacado para OTP y Alertas
                theme.card(self._featured_container, padding=10),
                # Tarjeta de Filtros y Tabla General
                theme.card(
                    ft.Column(
                        spacing=10,
                        expand=True,
                        controls=[
                            ft.Row(
                                wrap=True,
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Row(
                                        wrap=True,
                                        spacing=8,
                                        controls=[
                                            self._filter_tipo,
                                            self._filter_app,
                                            self._filter_date,
                                            self._btn_reload,
                                        ],
                                    ),
                                ],
                            ),
                            self._table_container,
                        ],
                    ),
                    padding=12,
                    expand=True,
                ),
            ],
        )

    # ---------------------------------------------------------- controles

    def _on_interval_change(self, _e: ft.ControlEvent) -> None:
        self._interval_label.value = f"Intervalo: {int(self._interval.value)} s"
        try:
            self._interval_label.update()
        except Exception:
            pass

    def _on_toggle(self, _e: ft.ControlEvent) -> None:
        if self._task and self._task.is_running:
            self._stop_monitor()
        else:
            self._start_monitor()

    def shutdown(self) -> None:
        """Detiene el monitor si sigue activo (al cerrar la app)."""
        self._stop_monitor()

    # ------------------------------------------------------- ciclo de vida

    def _start_monitor(self) -> None:
        status = self._device.get_status()
        if status.requires_selection:
            self._state_chip.value = "Selecciona dispositivo"
            self._state_chip.color = theme.ERR
            self._safe_update()
            return

        serial = self._device.serial
        if not serial or not status.connected:
            self._state_chip.value = "Sin dispositivo listo"
            self._state_chip.color = theme.ERR
            self._safe_update()
            return

        interval = float(self._interval.value)
        use_ai = bool(self._ai_switch.value)

        # Monitor configurado para guardar en carpeta independiente 'notifications'
        self._monitor = UiNotificationMonitor(
            serial=serial,
            logs_dir=str(NOTIFICATIONS_DIR),
            poll_interval=interval,
            use_ai=use_ai,
            on_notification_callback=self._on_any_notification,
            on_important=self._on_important,
        )
        self._task = BackgroundTask(
            "monitor-notificaciones",
            target=lambda _stop: self._monitor.start(),
            on_done=self._on_task_done,
            on_stop=self._monitor.stop,
        )

        self._device.register_supervisor(
            serial,
            on_stop=self._stop_monitor,
            on_restart=self._start_monitor,
        )

        self._new_count = self._important_count = 0
        self._update_stats()
        self._set_running(True)
        self._task.start()

    def _stop_monitor(self) -> None:
        if self._task and self._task.is_running:
            self._state_chip.value = "Deteniendo..."
            self._toggle_btn.disabled = True
            self._safe_update()
            self._task.stop()

    def _on_task_done(self, error: Optional[BaseException]) -> None:
        self._set_running(False)
        if error:
            self._state_chip.value = f"Error: {error}"
            self._state_chip.color = theme.ERR
            self._safe_update()

    # ----------------------------------------------------------- eventos

    def _on_any_notification(self, item: Any) -> None:
        self._new_count += 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event = {
            "fecha": now_str,
            "tipo": "NORMAL",
            "codigo": "",
            "fuente": "live",
            "titulo": getattr(item, "title", ""),
            "texto": getattr(item, "text", ""),
            "package": getattr(item, "package", ""),
        }
        self._all_events.insert(0, event)
        if len(self._all_events) > MAX_DISPLAY_RECORDS:
            self._all_events.pop()
        self._update_stats()
        self._render_table()

    def _on_important(self, event: Dict[str, Any]) -> None:
        self._important_count += 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "fecha": now_str,
            "tipo": event.get("tipo", "IMPORTANTE"),
            "codigo": event.get("codigo", ""),
            "fuente": event.get("fuente", "reglas"),
            "titulo": event.get("titulo", ""),
            "texto": event.get("texto", ""),
            "package": event.get("package", ""),
        }
        # Insertar al inicio
        self._all_events.insert(0, entry)
        if len(self._all_events) > MAX_DISPLAY_RECORDS:
            self._all_events.pop()
        self._update_stats()
        self._render_featured()
        self._render_table()

    # ----------------------------------------------------- persistencia y carga

    def _load_persisted_notifications(self) -> None:
        """Carga eventos históricos desde 'notifications/' o retrocompatible con 'logs/'."""
        events: List[Dict[str, Any]] = []

        # 1. Cargar importantes
        candidate_important = [
            NOTIFICATIONS_DIR / "notificaciones_importantes.json",
            Path("logs/notificaciones_importantes.json"),
        ]
        for p in candidate_important:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for item in data:
                            notif = item.get("notification", {})
                            cls = item.get("classification", {})
                            events.append({
                                "fecha": item.get("detected_at", ""),
                                "tipo": item.get("tipo") or cls.get("tipo") or "IMPORTANTE",
                                "codigo": item.get("codigo") or cls.get("codigo") or "",
                                "fuente": item.get("fuente") or cls.get("fuente") or "",
                                "titulo": notif.get("title", ""),
                                "texto": notif.get("text", ""),
                                "package": notif.get("package", ""),
                            })
                        break
                except Exception as exc:
                    logger.debug("Error leyendo %s: %s", p, exc)

        # 2. Cargar notificaciones diarias
        daily_files = list(NOTIFICATIONS_DIR.glob("notifications_*.json"))
        if not daily_files:
            daily_files = list(Path("logs").glob("notifications_*.json"))
        for df in sorted(daily_files, reverse=True)[:3]:  # Últimos 3 días
            try:
                d_data = json.loads(df.read_text(encoding="utf-8"))
                if isinstance(d_data, list):
                    for item in d_data:
                        events.append({
                            "fecha": item.get("timestamp") or item.get("detected_at", ""),
                            "tipo": item.get("tipo", "NORMAL"),
                            "codigo": item.get("codigo", ""),
                            "fuente": item.get("fuente", "diario"),
                            "titulo": item.get("title", ""),
                            "texto": item.get("text", ""),
                            "package": item.get("package", ""),
                        })
            except Exception:
                pass

        # Ordenar por fecha descendente y deduplicar
        events.sort(key=lambda x: str(x.get("fecha", "")), reverse=True)
        self._all_events = events[:MAX_DISPLAY_RECORDS]
        self._render_featured()
        self._render_table()

    # -------------------------------------------------------------- UI

    def _on_filter_changed(self, _e: ft.ControlEvent) -> None:
        self._render_featured()
        self._render_table()

    def _render_featured(self) -> None:
        """Muestra tarjetas destacadas para las alertas críticas y OTPs más recientes."""
        important_items = [e for e in self._all_events if e.get("tipo") in ("OTP", "ALERTA")]
        self._featured_panel.controls.clear()

        if not important_items:
            self._featured_container.visible = False
            try:
                self._featured_container.update()
            except Exception:
                pass
            return

        self._featured_container.visible = True
        for ev in important_items[:6]:  # Últimos 6 destacados
            tipo = ev.get("tipo", "ALERTA")
            color = theme.OK if tipo == "OTP" else theme.WARN
            codigo = ev.get("codigo")

            card_content = [
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Container(
                            content=ft.Text(tipo, size=10, weight=ft.FontWeight.BOLD, color=theme.BG),
                            bgcolor=color,
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                        ),
                        ft.Text(str(ev.get("fecha", ""))[-8:], size=10, color=theme.TEXT_MUTED),
                    ],
                ),
                ft.Text(
                    ev.get("titulo") or "(Sin título)",
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    max_lines=1,
                ),
            ]
            if codigo:
                card_content.append(
                    ft.Text(f"CÓDIGO: {codigo}", size=15, weight=ft.FontWeight.BOLD, color=color, selectable=True)
                )
            card_content.append(
                ft.Text(
                    ev.get("texto") or "",
                    size=11,
                    color=theme.TEXT,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    max_lines=2,
                )
            )
            card_content.append(
                ft.Text(ev.get("package") or "", size=10, color=theme.TEXT_MUTED)
            )

            box = ft.Container(
                content=ft.Column(card_content, spacing=2),
                width=240,
                padding=8,
                bgcolor=theme.SURFACE_ALT,
                border_radius=8,
                border=ft.Border.all(1, color),
            )
            self._featured_panel.controls.append(box)

        try:
            self._featured_container.update()
        except Exception:
            pass

    def _render_table(self) -> None:
        """Aplica filtros y genera la tabla navegable de notificaciones."""
        tipo_val = self._filter_tipo.value or "TODOS"
        app_val = (self._filter_app.value or "").strip().lower()
        date_val = (self._filter_date.value or "").strip().lower()

        filtered = []
        for ev in self._all_events:
            ev_tipo = ev.get("tipo", "NORMAL")
            if tipo_val != "TODOS" and ev_tipo != tipo_val:
                continue
            if app_val:
                pkg = str(ev.get("package", "")).lower()
                tit = str(ev.get("titulo", "")).lower()
                if app_val not in pkg and app_val not in tit:
                    continue
            if date_val:
                fec = str(ev.get("fecha", "")).lower()
                if date_val not in fec:
                    continue
            filtered.append(ev)

        self._table_container.controls.clear()

        if not filtered:
            self._table_container.controls.append(self._empty_label)
        else:
            columns = [
                ft.DataColumn(ft.Text("Fecha/Hora", weight=ft.FontWeight.BOLD, size=11)),
                ft.DataColumn(ft.Text("Tipo", weight=ft.FontWeight.BOLD, size=11)),
                ft.DataColumn(ft.Text("Aplicación", weight=ft.FontWeight.BOLD, size=11)),
                ft.DataColumn(ft.Text("Título / Código", weight=ft.FontWeight.BOLD, size=11)),
                ft.DataColumn(ft.Text("Mensaje", weight=ft.FontWeight.BOLD, size=11)),
            ]
            rows = []
            for ev in filtered[:150]:
                ev_tipo = ev.get("tipo", "NORMAL")
                tipo_color = (
                    theme.OK if ev_tipo == "OTP"
                    else (theme.WARN if ev_tipo == "ALERTA"
                    else theme.TEXT_MUTED)
                )

                cod = ev.get("codigo")
                tit_content = ev.get("titulo") or ""
                if cod:
                    tit_content = f"[{cod}] {tit_content}"

                rows.append(
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(str(ev.get("fecha", "")), size=11)),
                            ft.DataCell(
                                ft.Container(
                                    content=ft.Text(ev_tipo, size=10, weight=ft.FontWeight.BOLD, color=theme.BG),
                                    bgcolor=tipo_color,
                                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                    border_radius=4,
                                )
                            ),
                            ft.DataCell(ft.Text(str(ev.get("package", "")), size=11, color=theme.TEXT_MUTED)),
                            ft.DataCell(
                                ft.Text(
                                    tit_content,
                                    size=11,
                                    weight=ft.FontWeight.BOLD if cod else ft.FontWeight.NORMAL,
                                    selectable=True,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                )
                            ),
                            ft.DataCell(
                                ft.Text(
                                    str(ev.get("texto", "")),
                                    size=11,
                                    selectable=True,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                )
                            ),
                        ]
                    )
                )

            dt = ft.DataTable(
                columns=columns,
                rows=rows,
                border=ft.Border.all(1, theme.BORDER),
                horizontal_lines=ft.BorderSide(1, theme.BORDER),
                column_spacing=14,
                data_row_min_height=32,
                data_row_max_height=48,
            )
            count_text = ft.Text(
                f"Mostrando {len(rows)} de {len(filtered)} notificaciones.",
                size=11,
                color=theme.TEXT_MUTED,
            )
            self._table_container.controls.extend([
                count_text,
                ft.Row([dt], scroll=ft.ScrollMode.AUTO),
            ])

        try:
            self._table_container.update()
        except Exception:
            pass

    # ----------------------------------------------------- apertura de archivos

    def _on_open_important_clicked(self, _e: ft.ControlEvent) -> None:
        p = NOTIFICATIONS_DIR / "notificaciones_importantes.json"
        if not p.exists():
            p = Path("logs/notificaciones_importantes.json")
        if p.exists():
            os.startfile(str(p.resolve()))

    def _on_open_daily_clicked(self, _e: ft.ControlEvent) -> None:
        today_file = NOTIFICATIONS_DIR / f"notifications_{datetime.now():%Y-%m-%d}.json"
        if not today_file.exists():
            today_file = Path("logs") / f"notifications_{datetime.now():%Y-%m-%d}.json"
        if today_file.exists():
            os.startfile(str(today_file.resolve()))
        elif NOTIFICATIONS_DIR.exists():
            os.startfile(str(NOTIFICATIONS_DIR.resolve()))

    def _on_open_folder_clicked(self, _e: ft.ControlEvent) -> None:
        NOTIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(NOTIFICATIONS_DIR.resolve()))

    def _set_running(self, running: bool) -> None:
        self._toggle_btn.disabled = False
        self._toggle_btn.content = "Apagar" if running else "Encender"
        self._toggle_btn.icon = ft.Icons.STOP if running else ft.Icons.PLAY_ARROW
        self._toggle_btn.bgcolor = theme.ERR if running else theme.OK
        self._state_chip.value = "Monitoreando..." if running else "Detenido"
        self._state_chip.color = theme.OK if running else theme.TEXT_MUTED
        self._interval.disabled = running
        self._ai_switch.disabled = running
        self._safe_update()

    def _update_stats(self) -> None:
        self._stats.value = f"Nuevas: {self._new_count}  |  Importantes: {self._important_count}"
        try:
            self._stats.update()
        except Exception:
            pass

    def _safe_update(self) -> None:
        try:
            self.view.update()
        except Exception:
            pass
