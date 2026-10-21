"""Servicio de generación y gestión de sugerencias de automatización con IA (Fase B).

Identifica oportunidades de automatización seguras a partir de:
- Alertas bancarias o transaccionales frecuentes en notificaciones.
- Conexión recurrente de dispositivos para sugerir respaldo o sincronización de fotos.
- Hallazgos de fallos persistentes en reportes de respaldo.

PRINCIPIO CRÍTICO DE SEGURIDAD:
- Las sugerencias NUNCA se aplican de forma automática (confirm_required=True).
- Solo se convierten en reglas ejecutables cuando el usuario las acepta explícitamente (accept_suggestion).
- Las sugerencias descartadas (dismiss_suggestion) entran en período de enfriamiento para no insistir.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .ai_report_models import (
    AutomationSuggestion,
    AutomationSuggestionStatus,
    BackupAnalysisReport,
)
from .automation_rules import (
    ActionType,
    AutomationAction,
    AutomationCondition,
    AutomationRule,
    TriggerType,
)
from .automation_service import AutomationService
from .database import SQLiteStore
from .ollama_client import OllamaClient, OllamaError

logger = logging.getLogger("ui.services.automation_suggestion_service")


class AutomationSuggestionService:
    """Motor de heurísticas y propuestas de reglas de automatización con aprobación humana."""

    def __init__(
        self,
        automation_service: Optional[AutomationService] = None,
        ollama_client: Optional[OllamaClient] = None,
        database: Optional[SQLiteStore] = None,
        db_store: Optional[SQLiteStore] = None,
        notifications_dir: Optional[Union[str, Path]] = None,
        model: str = "qwen2.5-coder:7b",
    ) -> None:
        self.automation = automation_service
        self.ollama = ollama_client or OllamaClient()
        self.db = database or db_store or SQLiteStore()
        self.notifications_dir = Path(notifications_dir) if notifications_dir else None
        self.model = model
        self._init_tables()

    def _init_tables(self) -> None:
        """Inicializa la tabla de persistencia de sugerencias."""
        schema = """
            CREATE TABLE IF NOT EXISTS automation_suggestions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                rationale TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                proposed_rule_json TEXT NOT NULL,
                confirm_required INTEGER NOT NULL,
                status TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                dismissed_until_utc TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_automation_suggestions_sig ON automation_suggestions(signature);
        """
        with self.db.connect() as conn:
            conn.executescript(schema)

    def _is_signature_active(self, signature: str) -> bool:
        """Comprueba si una firma ya existe como pendiente, aceptada o descartada en enfriamiento."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT status, dismissed_until_utc FROM automation_suggestions
                WHERE signature = ?
                ORDER BY created_at_utc DESC LIMIT 1
                """,
                (signature,),
            ).fetchone()
            if not row:
                return False

            status_val, dismissed_until = row[0], row[1]
            if status_val == AutomationSuggestionStatus.PENDING.value:
                return True
            if status_val == AutomationSuggestionStatus.ACCEPTED.value:
                return True
            if status_val == AutomationSuggestionStatus.DISMISSED.value:
                if dismissed_until and dismissed_until > now_iso:
                    return True  # En período de enfriamiento
        return False

    def _save_suggestion(self, suggestion: AutomationSuggestion) -> None:
        """Guarda o actualiza una sugerencia en SQLite."""
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_suggestions(
                    id, title, rationale, trigger_type, proposed_rule_json,
                    confirm_required, status, signature, created_at_utc, dismissed_until_utc
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    rationale=excluded.rationale,
                    status=excluded.status,
                    dismissed_until_utc=excluded.dismissed_until_utc
                """,
                (
                    suggestion.id,
                    suggestion.title,
                    suggestion.rationale,
                    suggestion.trigger_type,
                    json.dumps(suggestion.proposed_rule, ensure_ascii=False),
                    1 if suggestion.confirm_required else 0,
                    suggestion.status.value,
                    suggestion.signature,
                    suggestion.created_at_utc,
                    suggestion.dismissed_until_utc,
                ),
            )

    def _refine_rationale_with_llm(self, default_rationale: str, context: str) -> str:
        """Enriquece opcionalmente el texto de justificación usando Ollama."""
        if not self.ollama.is_available():
            return default_rationale

        prompt = (
            f"Contexto observado en el dispositivo Android: {context}\n"
            f"Justificación técnica base: {default_rationale}\n\n"
            "Redacta una explicación concisa y clara en español (máximo 2 oraciones amigables) "
            "explicando al usuario por qué activar esta regla de automatización le resultará útil y conveniente."
        )
        try:
            enhanced = self.ollama.generate(prompt=prompt, model=self.model, temperature=0.3, timeout=15.0)
            if enhanced and len(enhanced.strip()) > 15:
                return enhanced.strip().strip('"')
        except Exception as exc:
            logger.debug("Ollama no pudo enriquecer rationale: %s", exc)
        return default_rationale

    def generate_suggestions(
        self,
        notifications: Optional[List[Dict[str, Any]]] = None,
        backup_report: Optional[BackupAnalysisReport] = None,
        connected_serials: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[AutomationSuggestion]:
        """
        Evalúa heurísticas sobre eventos pasados para proponer sugerencias accionables.
        Ninguna propuesta se aplica automáticamente.
        """
        new_suggestions: List[AutomationSuggestion] = []

        # Cargar notificaciones de disco si no se pasaron y existe notifications_dir
        if notifications is None and self.notifications_dir and self.notifications_dir.is_dir():
            notifications = []
            imp_file = self.notifications_dir / "notificaciones_importantes.json"
            if imp_file.is_file():
                try:
                    data = json.loads(imp_file.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        notifications.extend(data)
                except Exception:
                    pass
            for f in self.notifications_dir.glob("notifications_*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        notifications.extend(data)
                except Exception:
                    pass

        # Procesar datos derivados de context
        if context:
            if not connected_serials and context.get("device_connected_count", 0) >= 3:
                connected_serials = ["primary_device"]
            if not backup_report and context.get("backup_report"):
                backup_report = context["backup_report"]

        # ---------------------------------------------------------------------
        # Heurística 1: Notificaciones de compras o bancos
        # ---------------------------------------------------------------------
        if notifications:
            bank_keywords = ["compra", "banco", "aprobada", "transferencia", "mercadopago", "bbva", "santander", "retiro"]
            has_financial_alerts = any(
                any(kw in f"{n.get('title', '')} {n.get('text', '')}".lower() for kw in bank_keywords)
                for n in notifications
            )

            if has_financial_alerts:
                sig = "notif_financial_alert_audit"
                if not self._is_signature_active(sig):
                    rule_id = f"rule_financial_{uuid.uuid4().hex[:6]}"
                    proposed_rule = AutomationRule(
                        id=rule_id,
                        name="Auditoría automática de alertas bancarias y compras",
                        trigger=TriggerType.NOTIFICATION_RECEIVED,
                        conditions=AutomationCondition(text_contains="compra"),
                        actions=[
                            AutomationAction(
                                action_type=ActionType.SAVE_NOTIFICATION,
                                params={"important": True, "category": "finanzas"},
                            ),
                            AutomationAction(
                                action_type=ActionType.LOG_SUMMARY,
                                params={"prefix": "[ALERTA_FINANCIERA]"},
                            ),
                        ],
                        enabled=True,
                    )

                    base_rationale = (
                        "Se detectaron notificaciones bancarias o de compras frecuentes en el dispositivo. "
                        "Esta regla guarda automáticamente un registro de auditoría local de dichas transacciones."
                    )
                    refined_rationale = self._refine_rationale_with_llm(
                        base_rationale,
                        "Múltiples alertas de compras y bancos detectadas en notificaciones."
                    )

                    sug = AutomationSuggestion(
                        id=f"sug_{uuid.uuid4().hex[:8]}",
                        title="Guardar automáticamente alertas bancarias y transacciones",
                        rationale=refined_rationale,
                        trigger_type=TriggerType.NOTIFICATION_RECEIVED.value,
                        proposed_rule=proposed_rule.model_dump(),
                        confirm_required=True,
                        status=AutomationSuggestionStatus.PENDING,
                        signature=sig,
                    )
                    self._save_suggestion(sug)
                    new_suggestions.append(sug)

        # ---------------------------------------------------------------------
        # Heurística 2: Dispositivo conectado frecuentemente -> sugerir sync de fotos
        # ---------------------------------------------------------------------
        if connected_serials:
            for serial in connected_serials:
                sig = f"device_connected_sync_photos_{serial}"
                if not self._is_signature_active(sig):
                    rule_id = f"rule_sync_photos_{uuid.uuid4().hex[:6]}"
                    proposed_rule = AutomationRule(
                        id=rule_id,
                        name=f"Sincronizar fotos al conectar {serial}",
                        trigger=TriggerType.DEVICE_CONNECTED,
                        conditions=AutomationCondition(serial=serial),
                        actions=[
                            AutomationAction(
                                action_type=ActionType.RUN_SYNC_PROFILE,
                                params={"profile_id": "photos_to_pc", "serial": serial},
                            )
                        ],
                        enabled=True,
                    )

                    base_rationale = (
                        f"El dispositivo '{serial}' se conecta periódicamente a esta PC. "
                        "Se sugiere iniciar automáticamente la sincronización incremental de fotos y videos al conectarlo."
                    )
                    refined_rationale = self._refine_rationale_with_llm(
                        base_rationale,
                        f"Dispositivo {serial} detectado con conexiones frecuentes."
                    )

                    sug = AutomationSuggestion(
                        id=f"sug_{uuid.uuid4().hex[:8]}",
                        title=f"Sincronizar fotos al detectar Conexión de Dispositivo ({serial})",
                        rationale=refined_rationale,
                        trigger_type=TriggerType.DEVICE_CONNECTED.value,
                        proposed_rule=proposed_rule.model_dump(),
                        confirm_required=True,
                        status=AutomationSuggestionStatus.PENDING,
                        signature=sig,
                    )
                    self._save_suggestion(sug)
                    new_suggestions.append(sug)

        # ---------------------------------------------------------------------
        # Heurística 3: Diagnóstico de fallos de respaldo
        # ---------------------------------------------------------------------
        if backup_report and backup_report.repeated_denied_paths:
            sig = f"backup_warning_check_{len(backup_report.repeated_denied_paths)}"
            if not self._is_signature_active(sig):
                rule_id = f"rule_backup_diag_{uuid.uuid4().hex[:6]}"
                proposed_rule = AutomationRule(
                    id=rule_id,
                    name="Diagnóstico de almacenamiento ante conexión",
                    trigger=TriggerType.DEVICE_CONNECTED,
                    conditions=AutomationCondition(),
                    actions=[
                        AutomationAction(
                            action_type=ActionType.SHELL_SAFE_COMMAND,
                            params={"command": "df -h /sdcard"},
                        )
                    ],
                    enabled=True,
                )

                base_rationale = (
                    f"El análisis de respaldos identificó {len(backup_report.repeated_denied_paths)} rutas con fallos persistentes. "
                    "Se propone ejecutar una comprobación de almacenamiento ligera al conectar el teléfono para verificar disponibilidad."
                )
                refined_rationale = self._refine_rationale_with_llm(
                    base_rationale,
                    "Fallos repetidos detectados en el análisis de respaldos anteriores."
                )

                sug = AutomationSuggestion(
                    id=f"sug_{uuid.uuid4().hex[:8]}",
                    title="Verificar almacenamiento al conectar por fallos en respaldos previos",
                    rationale=refined_rationale,
                    trigger_type=TriggerType.DEVICE_CONNECTED.value,
                    proposed_rule=proposed_rule.model_dump(),
                    confirm_required=True,
                    status=AutomationSuggestionStatus.PENDING,
                    signature=sig,
                )
                self._save_suggestion(sug)
                new_suggestions.append(sug)

        return new_suggestions

    def list_suggestions(
        self,
        status_filter: Optional[AutomationSuggestionStatus] = None,
    ) -> List[AutomationSuggestion]:
        """Lista las sugerencias registradas con filtro de estado opcional."""
        with self.db.connect() as conn:
            if status_filter:
                rows = conn.execute(
                    """
                    SELECT id, title, rationale, trigger_type, proposed_rule_json,
                           confirm_required, status, signature, created_at_utc, dismissed_until_utc
                    FROM automation_suggestions WHERE status = ?
                    ORDER BY created_at_utc DESC
                    """,
                    (status_filter.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, title, rationale, trigger_type, proposed_rule_json,
                           confirm_required, status, signature, created_at_utc, dismissed_until_utc
                    FROM automation_suggestions
                    ORDER BY created_at_utc DESC
                    """
                ).fetchall()

            results: List[AutomationSuggestion] = []
            for r in rows:
                rule_data = json.loads(r[4])
                results.append(
                    AutomationSuggestion(
                        id=r[0],
                        title=r[1],
                        rationale=r[2],
                        trigger_type=r[3],
                        proposed_rule=rule_data,
                        confirm_required=bool(r[5]),
                        status=AutomationSuggestionStatus(r[6]),
                        signature=r[7],
                        created_at_utc=r[8],
                        dismissed_until_utc=r[9],
                    )
                )
            return results

    def get_suggestion(self, suggestion_id: str) -> Optional[AutomationSuggestion]:
        """Obtiene una sugerencia por su ID."""
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT id, title, rationale, trigger_type, proposed_rule_json,
                       confirm_required, status, signature, created_at_utc, dismissed_until_utc
                FROM automation_suggestions WHERE id = ?
                """,
                (suggestion_id,),
            ).fetchone()
            if not row:
                return None
            return AutomationSuggestion(
                id=row[0],
                title=row[1],
                rationale=row[2],
                trigger_type=row[3],
                proposed_rule=json.loads(row[4]),
                confirm_required=bool(row[5]),
                status=AutomationSuggestionStatus(row[6]),
                signature=row[7],
                created_at_utc=row[8],
                dismissed_until_utc=row[9],
            )

    def accept_suggestion(self, suggestion_id: str) -> Optional[AutomationSuggestion]:
        """
        Acepta explícitamente una sugerencia.
        SOLO en este momento se crea la regla en el servicio AutomationService.
        """
        sug = self.get_suggestion(suggestion_id)
        if not sug:
            logger.warning("No se encontró la sugerencia %s para aceptar.", suggestion_id)
            return None

        sug.status = AutomationSuggestionStatus.ACCEPTED
        self._save_suggestion(sug)
        logger.info("Sugerencia %s aceptada por el usuario.", suggestion_id)

        # Construir y guardar la regla en AutomationService si está inyectado
        if self.automation:
            try:
                created_rule = AutomationRule.model_validate(sug.proposed_rule)
                self.automation.save_rule(created_rule)
                logger.info("Regla '%s' activada exitosamente en AutomationService.", created_rule.name)
            except Exception as exc:
                logger.error("Error al registrar la regla de la sugerencia %s: %s", suggestion_id, exc)

        return sug

    def dismiss_suggestion(self, suggestion_id: str, cooldown_days: int = 7) -> Optional[AutomationSuggestion]:
        """
        Descarta una sugerencia y establece un período de enfriamiento para no volver a proponerla.
        """
        sug = self.get_suggestion(suggestion_id)
        if not sug:
            return None

        cooldown_until = (datetime.now(timezone.utc) + timedelta(days=cooldown_days)).isoformat()
        sug.status = AutomationSuggestionStatus.DISMISSED
        sug.dismissed_until_utc = cooldown_until
        self._save_suggestion(sug)
        logger.info("Sugerencia %s descartada. Cooldown hasta %s", suggestion_id, cooldown_until)
        return sug
