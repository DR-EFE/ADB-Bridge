"""Servicio de generación de resúmenes diarios de notificaciones con IA (Fase B).

Procesa los registros de notificaciones diarias (notifications_YYYY-MM-DD.json, notificaciones_importantes.json),
calcula métricas de volumen, detecta códigos OTP y alertas críticas, y genera viñetas ejecutivas en español
mediante un modelo local en Ollama (con fallback extractivo determinista automático).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .ai_report_models import DaySummary
from .ollama_client import OllamaClient, OllamaError

logger = logging.getLogger("ui.services.ai_summary_service")

# Expresiones regulares para detección determinista de códigos OTP y alertas
OTP_REGEX = re.compile(r"\b(?:\d{4,8}|[A-Z0-9]{5,8})\b", re.IGNORECASE)
OTP_KEYWORDS = {"codigo", "código", "verificacion", "verificación", "clave", "otp", "token", "seguridad"}
ALERT_KEYWORDS = {"compra", "aprobada", "transferencia", "banco", "tarjeta", "retiro", "alerta", "fraude", "urgente", "bloqueo"}


class AiSummaryService:
    """Generador bajo demanda de resúmenes diarios de notificaciones."""

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        notifications_dir: Optional[Path | str] = None,
        model: str = "qwen2.5-coder:7b",
    ) -> None:
        self.ollama = ollama_client or OllamaClient()
        self.notifications_dir = Path(notifications_dir or "notifications")
        self.model = model

    def _extract_notifications(self, date_str: str) -> List[Dict[str, Any]]:
        """Lee el archivo principal del día y las notificaciones importantes de esa fecha."""
        all_notifications: List[Dict[str, Any]] = []
        seen_keys: Set[str] = set()

        # 1. Archivo diario: notifications_YYYY-MM-DD.json
        daily_file = self.notifications_dir / f"notifications_{date_str}.json"
        if daily_file.is_file():
            try:
                data = json.loads(daily_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            key = item.get("key") or f"{item.get('package')}_{item.get('timestamp')}_{item.get('title')}"
                            if key not in seen_keys:
                                seen_keys.add(key)
                                all_notifications.append(item)
            except Exception as exc:
                logger.warning("Error leyendo %s: %s", daily_file, exc)

        # 2. Archivo general de importantes: notificaciones_importantes.json
        imp_file = self.notifications_dir / "notificaciones_importantes.json"
        if imp_file.is_file():
            try:
                imp_data = json.loads(imp_file.read_text(encoding="utf-8"))
                if isinstance(imp_data, list):
                    for entry in imp_data:
                        if not isinstance(entry, dict):
                            continue
                        # Verificar fecha
                        det_at = str(entry.get("detected_at") or "")
                        notif = entry.get("notification") or entry
                        notif_ts = str(notif.get("timestamp") or "")
                        if det_at.startswith(date_str) or notif_ts.startswith(date_str):
                            key = notif.get("key") or f"{notif.get('package')}_{notif_ts}_{notif.get('title')}"
                            if key not in seen_keys:
                                seen_keys.add(key)
                                # Conservar metadatos de clasificación si existen
                                if "classification" in entry:
                                    notif["classification"] = entry["classification"]
                                if "tipo" in entry and "tipo" not in notif:
                                    notif["tipo"] = entry["tipo"]
                                all_notifications.append(notif)
            except Exception as exc:
                logger.warning("Error leyendo %s: %s", imp_file, exc)

        return all_notifications

    def _is_otp(self, notif: Dict[str, Any]) -> bool:
        """Determina si la notificación contiene un código OTP."""
        tipo = str(notif.get("tipo") or "").upper()
        if tipo == "OTP":
            return True
        classification = notif.get("classification")
        if isinstance(classification, dict):
            if str(classification.get("tipo") or "").upper() == "OTP":
                return True
        elif isinstance(classification, str):
            if classification.upper() == "OTP":
                return True

        text_content = f"{notif.get('title', '')} {notif.get('text', '')}".lower()
        if any(kw in text_content for kw in OTP_KEYWORDS) and OTP_REGEX.search(text_content):
            return True
        return False

    def _is_urgent(self, notif: Dict[str, Any]) -> bool:
        """Determina si la notificación es una alerta bancaria, de compra o urgente."""
        tipo = str(notif.get("tipo") or "").upper()
        if tipo in ("ALERTA", "URGENTE", "BANCO", "COMPRA", "BANCO_ALERTA"):
            return True
        classification = notif.get("classification")
        if isinstance(classification, dict):
            if classification.get("importante") is True or str(classification.get("tipo") or "").upper() in ("CRITICAL", "ALERTA", "URGENTE"):
                return True
        elif isinstance(classification, str):
            if classification.upper() in ("CRITICAL", "ALERTA", "URGENTE", "IMPORTANT"):
                return True

        text_content = f"{notif.get('title', '')} {notif.get('text', '')}".lower()
        return any(kw in text_content for kw in ALERT_KEYWORDS)

    def _generate_extractive_highlights(
        self,
        date_str: str,
        total: int,
        top_apps: List[tuple[str, int]],
        urgent_items: List[Dict[str, Any]],
        otp_items: List[Dict[str, Any]],
    ) -> List[str]:
        """Genera viñetas deterministas en español sin requerir LLM."""
        bullets: List[str] = [
            f"Se registraron {total} notificaciones en total a lo largo del día {date_str}."
        ]
        if top_apps:
            apps_text = ", ".join(f"{pkg.split('.')[-1]} ({count})" for pkg, count in top_apps[:3])
            bullets.append(f"Aplicaciones con mayor volumen de notificaciones: {apps_text}.")

        if urgent_items:
            bullets.append(f"Se identificaron {len(urgent_items)} alertas importantes o bancarias.")
            sample_titles = [str(n.get("title", "")).strip() for n in urgent_items[:2] if n.get("title")]
            if sample_titles:
                bullets.append(f"Alertas destacadas: {'; '.join(sample_titles)}.")

        if otp_items:
            bullets.append(f"Se recibieron {len(otp_items)} códigos de verificación (OTP) o autenticación.")

        return bullets

    def _generate_llm_highlights(
        self,
        date_str: str,
        total: int,
        top_apps: List[tuple[str, int]],
        urgent_items: List[Dict[str, Any]],
        otp_items: List[Dict[str, Any]],
    ) -> Optional[List[str]]:
        """Invoca a Ollama para sintetizar un resumen ejecutivo en viñetas en español."""
        if not self.ollama.is_available():
            return None

        apps_summary = ", ".join(f"{pkg} ({cnt})" for pkg, cnt in top_apps[:5])
        urgent_samples = "\n".join(
            f"- [{n.get('package', 'app')}] {n.get('title', '')}: {n.get('text', '')}"
            for n in (urgent_items + otp_items)[:6]
        )

        prompt = (
            f"Analiza la siguiente actividad de notificaciones del dispositivo Android para la fecha {date_str}:\n"
            f"- Total de notificaciones: {total}\n"
            f"- Apps con más actividad: {apps_summary}\n"
            f"- Notificaciones urgentes y códigos OTP detectados:\n{urgent_samples or '(Ninguna relevante)'}\n\n"
            "Redacta un resumen ejecutivo en exactamente 3 a 5 viñetas concisas en español. "
            "Cada viñeta debe comenzar con un guión (-) y destacar el volumen general, "
            "las alertas de seguridad o compras, y la presencia de códigos de verificación. "
            "No agregues texto introductorio ni conclusiones."
        )

        try:
            raw_response = self.ollama.generate(
                prompt=prompt,
                model=self.model,
                temperature=0.2,
                timeout=25.0,
            )
            bullets = [
                line.strip().lstrip("-*•0123456789. ")
                for line in raw_response.splitlines()
                if line.strip().startswith(("-", "*", "•")) or re.match(r"^\d+\.", line.strip())
            ]
            valid_bullets = [b for b in bullets if len(b) > 10]
            if len(valid_bullets) >= 2:
                return valid_bullets
        except OllamaError as exc:
            logger.info("Ollama no completó la generación de resumen: %s; usando resumen extractivo.", exc)
        except Exception as exc:
            logger.warning("Fallo al consultar Ollama para resumen diario: %s", exc)

        return None

    def generate_day_summary(self, date_str: str) -> DaySummary:
        """
        Genera el resumen de notificaciones para la fecha especificada (formato YYYY-MM-DD).
        Persiste el resultado en notifications/summary_YYYY-MM-DD.json.
        """
        notifications = self._extract_notifications(date_str)
        total = len(notifications)

        app_counter = Counter(
            n.get("package") or n.get("pkg") or n.get("appName") or "desconocido"
            for n in notifications
        )
        app_counts = dict(app_counter)
        top_apps = app_counter.most_common(5)

        urgent_items: List[Dict[str, Any]] = []
        otp_items: List[Dict[str, Any]] = []

        for n in notifications:
            is_otp = self._is_otp(n)
            is_urg = self._is_urgent(n)
            if is_otp:
                otp_items.append(n)
            if is_urg:
                urgent_items.append(n)

        # Resumen estructurado de urgentes y OTPs
        combined_critical: List[Dict[str, Any]] = []
        for item in (urgent_items + otp_items)[:20]:
            critical_entry = {
                "package": item.get("package") or item.get("pkg") or item.get("appName"),
                "title": item.get("title"),
                "text": item.get("text"),
                "timestamp": item.get("timestamp") or item.get("time"),
                "tipo": "OTP" if self._is_otp(item) else "ALERTA",
            }
            if critical_entry not in combined_critical:
                combined_critical.append(critical_entry)

        # Intentar síntesis con LLM (solo si hay actividad), con fallback extractivo
        highlights = self._generate_llm_highlights(date_str, total, top_apps, urgent_items, otp_items) if total > 0 else None
        if highlights:
            generated_by_llm = True
            model_used = self.model
        else:
            highlights = self._generate_extractive_highlights(date_str, total, top_apps, urgent_items, otp_items)
            generated_by_llm = False
            model_used = None

        summary = DaySummary(
            date=date_str,
            total_notifications=total,
            app_counts=app_counts,
            urgent_count=len(urgent_items),
            otp_count=len(otp_items),
            urgent_and_otp_items=combined_critical,
            highlights=highlights,
            generated_by_llm=generated_by_llm,
            model_used=model_used,
        )

        # Guardar en notifications/summary_YYYY-MM-DD.json
        self.notifications_dir.mkdir(parents=True, exist_ok=True)
        summary_file = self.notifications_dir / f"summary_{date_str}.json"
        try:
            summary_file.write_text(json.dumps(summary.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Resumen diario guardado en %s (LLM: %s)", summary_file, generated_by_llm)
        except Exception as exc:
            logger.error("No se pudo guardar %s: %s", summary_file, exc)

        return summary
