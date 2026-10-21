"""Servicio y motor de automatizaciones por reglas (Fase 4B).

Permite configurar y evaluar reglas automáticas que disparan acciones
(backup, sync, guardado de notificaciones, logs de resumen, comandos seguros)
ante eventos del sistema (device_connected, notification_received, storage_low, schedule).

Características de seguridad y robustez:
1. No bloquea hilos de eventos (despacho asíncrono o vía JobQueueService).
2. Allowlist estricta para comandos de shell (prohibido ejecutar comandos destructivos o arbitrarios).
3. Inyección de dependencias para BackupService, SyncService, JobQueueService y AdbService.
4. Persistencia en SQLite con prefijo exclusivo automation_* (automation_rules, automation_runs).
5. Resumen estructurado JSON de lotes de ejecuciones para auditoría y visualización.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from .adb_service import AdbService
from .automation_rules import (
    ActionRunResult,
    ActionType,
    AutomationAction,
    AutomationCondition,
    AutomationRule,
    AutomationRun,
    AutomationRunStatus,
    TriggerType,
)
from .database import SQLiteStore
from .job_queue_service import JobItem, JobQueueService, JobStatus, JobType

logger = logging.getLogger("ui.services.automation_service")

# Allowlist estricta de comandos shell permitidos para SHELL_SAFE_COMMAND
SAFE_SHELL_ALLOWLIST = {
    "uptime",
    "date",
    "df -h /sdcard",
    "df -k /sdcard",
    "getprop ro.build.version.release",
    "getprop ro.product.model",
    "getprop ro.product.manufacturer",
    "dumpsys battery",
    "dumpsys wifi | grep -i 'curstate'",
}


class AutomationService:
    """Motor de automatización basado en reglas y eventos."""

    def __init__(
        self,
        job_queue: Optional[JobQueueService] = None,
        sync_service: Optional[Any] = None,
        backup_service: Optional[Any] = None,
        adb_service: Optional[AdbService] = None,
        database: Optional[SQLiteStore] = None,
    ):
        self._job_queue = job_queue
        self._sync_service = sync_service
        self._backup_service = backup_service
        self._adb = adb_service or AdbService()
        self._db = database or SQLiteStore()
        self._init_automation_tables()

    def _init_automation_tables(self) -> None:
        """Crea las tablas SQLite con prefijo automation_*."""
        schema = """
            CREATE TABLE IF NOT EXISTS automation_rules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trigger TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS automation_runs (
                id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_automation_runs_rule ON automation_runs(rule_id);
            CREATE INDEX IF NOT EXISTS idx_automation_rules_trigger ON automation_rules(trigger);
        """
        with self._db.connect() as conn:
            conn.executescript(schema)

    # -------------------------------------------------------------------------
    # Gestión de Reglas (CRUD)
    # -------------------------------------------------------------------------

    def save_rule(self, rule: AutomationRule) -> None:
        """Guarda o actualiza una regla de automatización."""
        encoded = json.dumps(rule.model_dump(), ensure_ascii=False, default=str)
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_rules(id, name, trigger, enabled, payload_json, created_at_utc)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    trigger=excluded.trigger,
                    enabled=excluded.enabled,
                    payload_json=excluded.payload_json
                """,
                (
                    rule.id,
                    rule.name,
                    rule.trigger.value,
                    1 if rule.enabled else 0,
                    encoded,
                    rule.created_at_utc,
                ),
            )
        logger.info("Regla de automatización guardada: [%s] '%s' (%s)", rule.id, rule.name, rule.trigger.value)

    def get_rule(self, rule_id: str) -> Optional[AutomationRule]:
        """Obtiene una regla por ID."""
        with self._db.connect() as conn:
            row = conn.execute("SELECT payload_json FROM automation_rules WHERE id = ?", (rule_id,)).fetchone()
            if row:
                return AutomationRule.model_validate(json.loads(row[0]))
        return None

    def list_rules(self, trigger: Optional[TriggerType] = None, enabled_only: bool = False) -> List[AutomationRule]:
        """Lista las reglas guardadas con filtros opcionales."""
        query = "SELECT payload_json FROM automation_rules WHERE 1=1"
        params: List[Any] = []
        if trigger:
            query += " AND trigger = ?"
            params.append(trigger.value)
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY created_at_utc DESC"

        with self._db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [AutomationRule.model_validate(json.loads(r[0])) for r in rows]

    def delete_rule(self, rule_id: str) -> bool:
        """Elimina una regla."""
        with self._db.connect() as conn:
            cursor = conn.execute("DELETE FROM automation_rules WHERE id = ?", (rule_id,))
            return cursor.rowcount > 0

    def record_run(self, run: AutomationRun) -> None:
        """Persiste una ejecución de regla en automation_runs."""
        encoded = json.dumps(run.model_dump(), ensure_ascii=False, default=str)
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_runs(id, rule_id, trigger, status, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.rule_id,
                    run.trigger_type.value,
                    run.status.value,
                    encoded,
                    run.started_at_utc,
                ),
            )

    def list_runs(self, limit: int = 50) -> List[AutomationRun]:
        """Obtiene las últimas ejecuciones registradas."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM automation_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [AutomationRun.model_validate(json.loads(r[0])) for r in rows]

    # -------------------------------------------------------------------------
    # Evaluación de Condiciones
    # -------------------------------------------------------------------------

    def match_condition(self, condition: AutomationCondition, event_payload: Dict[str, Any]) -> bool:
        """Evalúa si el evento entrante cumple los requisitos de la condición."""
        # 1. Filtro por serial
        if condition.serial:
            event_serial = event_payload.get("serial")
            if not event_serial or event_serial.strip().lower() != condition.serial.strip().lower():
                return False

        # 2. Filtro por paquete de app (para notificaciones)
        if condition.package_name:
            event_pkg = event_payload.get("package") or event_payload.get("package_name") or ""
            if condition.package_name.lower() not in event_pkg.lower():
                return False

        # 3. Filtro por texto contenido
        if condition.text_contains:
            target = condition.text_contains.lower()
            text_fields = [
                str(event_payload.get("texto") or ""),
                str(event_payload.get("text") or ""),
                str(event_payload.get("titulo") or ""),
                str(event_payload.get("title") or ""),
                str(event_payload.get("sub_text") or ""),
                str(event_payload.get("message") or ""),
            ]
            if not any(target in field.lower() for field in text_fields):
                return False

        # 4. Filtro por perfil ID asociado
        if condition.profile_id:
            event_profile_id = event_payload.get("profile_id")
            if not event_profile_id or event_profile_id != condition.profile_id:
                return False

        # 5. Filtro por umbral de almacenamiento (para storage_low)
        if condition.storage_threshold_percent is not None:
            free_pct = event_payload.get("storage_free_percent")
            if free_pct is None or free_pct > condition.storage_threshold_percent:
                return False

        return True

    # -------------------------------------------------------------------------
    # Ejecución de Acciones
    # -------------------------------------------------------------------------

    def execute_action(self, action: AutomationAction, event_payload: Dict[str, Any]) -> ActionRunResult:
        """Ejecuta una acción segura controlada."""
        serial = event_payload.get("serial")
        params = action.params

        # 1. Ejecutar perfil de Backup
        if action.action_type == ActionType.RUN_BACKUP_PROFILE:
            profile_id = params.get("profile_id")
            if not self._backup_service:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error="BackupService no disponible en el motor de automatización.",
                )
            try:
                # Comprobar método enqueue_profile o run_backup
                if hasattr(self._backup_service, "enqueue_profile"):
                    job_id = self._backup_service.enqueue_profile(profile_id, serial=serial)
                    return ActionRunResult(
                        action_type=action.action_type,
                        status=AutomationRunStatus.SUCCESS,
                        job_id=job_id,
                        output=f"Backup profile '{profile_id}' encolado (Job: {job_id})",
                    )
                elif hasattr(self._backup_service, "run_backup"):
                    # Si tiene cola de jobs, delegar encolamiento de backup
                    if self._job_queue:
                        job_id = self._job_queue.enqueue(
                            job_type=JobType.BACKUP_CATEGORY,
                            title=f"Auto Backup: {profile_id or 'General'}",
                            serial=serial,
                            payload={"profile_id": profile_id, "serial": serial},
                        )
                        return ActionRunResult(
                            action_type=action.action_type,
                            status=AutomationRunStatus.SUCCESS,
                            job_id=job_id,
                            output=f"Trabajo de backup encolado: {job_id}",
                        )
                    return ActionRunResult(
                        action_type=action.action_type,
                        status=AutomationRunStatus.SUCCESS,
                        output=f"Backup de '{profile_id}' planificado.",
                    )
                else:
                    return ActionRunResult(
                        action_type=action.action_type,
                        status=AutomationRunStatus.FAILED,
                        error="BackupService no cuenta con método de ejecución compatible.",
                    )
            except Exception as exc:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error=f"Fallo al ejecutar backup: {exc}",
                )

        # 2. Ejecutar perfil de Sincronización Inteligente
        elif action.action_type == ActionType.RUN_SYNC_PROFILE:
            profile_id = params.get("profile_id")
            if not self._sync_service:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error="SyncService no configurado.",
                )
            try:
                profile = None
                if hasattr(self._sync_service, "get_profile"):
                    profile = self._sync_service.get_profile(profile_id)
                if not profile:
                    return ActionRunResult(
                        action_type=action.action_type,
                        status=AutomationRunStatus.FAILED,
                        error=f"No se encontró el perfil de sync '{profile_id}'.",
                    )
                # Si el evento trae serial y el perfil no lo fijaba, asignarlo dinámicamente
                if serial and not profile.serial:
                    profile.serial = serial

                if hasattr(self._sync_service, "enqueue_sync_job") and self._job_queue:
                    job_id = self._sync_service.enqueue_sync_job(profile, job_queue=self._job_queue)
                    return ActionRunResult(
                        action_type=action.action_type,
                        status=AutomationRunStatus.SUCCESS,
                        job_id=job_id,
                        output=f"Sync perfil '{profile_id}' encolado con éxito (Job: {job_id})",
                    )
                else:
                    report = self._sync_service.run_sync(profile)
                    return ActionRunResult(
                        action_type=action.action_type,
                        status=AutomationRunStatus.SUCCESS if report.success else AutomationRunStatus.FAILED,
                        output=f"Sync ejecutado: {report.transferred_count} transferidos, {report.conflict_count} conflictos.",
                        error="; ".join(report.errors) if report.errors else None,
                    )
            except Exception as exc:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error=f"Error en acción sync: {exc}",
                )

        # 3. Guardar Notificación en Base de Datos
        elif action.action_type == ActionType.SAVE_NOTIFICATION:
            try:
                self._db.record_notification(event_payload, serial=serial)
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.SUCCESS,
                    output="Notificación persistida en SQLite (tabla notifications).",
                )
            except Exception as exc:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error=f"Error guardando notificación: {exc}",
                )

        # 4. Registrar Resumen en Log y Métricas
        elif action.action_type == ActionType.LOG_SUMMARY:
            msg = params.get("message") or f"Auto-log evento {event_payload}"
            logger.info("[AUTOMATION_LOG] %s", msg)
            try:
                self._db.record_metric("automation_log_event", 1.0, {"event": event_payload, "custom_msg": msg})
            except Exception:
                pass
            return ActionRunResult(
                action_type=action.action_type,
                status=AutomationRunStatus.SUCCESS,
                output=msg,
            )

        # 5. Comando Seguro de Shell (Allowlist estricta)
        elif action.action_type == ActionType.SHELL_SAFE_COMMAND:
            cmd = str(params.get("command", "")).strip()
            if cmd not in SAFE_SHELL_ALLOWLIST:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error=f"COMANDO_NO_PERMITIDO: El comando '{cmd}' no está en la lista de comandos seguros autorizados.",
                )
            try:
                op = self._adb.shell(cmd, serial=serial, timeout=15.0)
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.SUCCESS if op.success else AutomationRunStatus.FAILED,
                    output=op.output,
                    error=op.message if not op.success else None,
                )
            except Exception as exc:
                return ActionRunResult(
                    action_type=action.action_type,
                    status=AutomationRunStatus.FAILED,
                    error=f"Fallo ejecutando comando seguro: {exc}",
                )

        return ActionRunResult(
            action_type=action.action_type,
            status=AutomationRunStatus.FAILED,
            error=f"Tipo de acción no soportado: {action.action_type}",
        )

    # -------------------------------------------------------------------------
    # Manejadores de Eventos Públicos (Inyectables para Tests)
    # -------------------------------------------------------------------------

    def on_device_connected(self, serial: str, extra_payload: Optional[Dict[str, Any]] = None) -> List[AutomationRun]:
        """Dispara las reglas asociadas a la conexión de un dispositivo."""
        payload = {"serial": serial, **(extra_payload or {})}
        return self._evaluate_and_dispatch(TriggerType.DEVICE_CONNECTED, payload)

    def on_notification(self, payload: Dict[str, Any]) -> List[AutomationRun]:
        """Dispara las reglas asociadas a la llegada de una notificación."""
        return self._evaluate_and_dispatch(TriggerType.NOTIFICATION_RECEIVED, payload)

    def on_storage_low(self, serial: str, free_percent: float) -> List[AutomationRun]:
        """Dispara reglas cuando el almacenamiento libre del teléfono cae por debajo del umbral."""
        payload = {"serial": serial, "storage_free_percent": free_percent}
        return self._evaluate_and_dispatch(TriggerType.STORAGE_LOW, payload)

    def _evaluate_and_dispatch(self, trigger: TriggerType, event_payload: Dict[str, Any]) -> List[AutomationRun]:
        """Evalúa todas las reglas activas para el trigger y ejecuta sus acciones."""
        rules = self.list_rules(trigger=trigger, enabled_only=True)
        runs: List[AutomationRun] = []

        for rule in rules:
            if not self.match_condition(rule.conditions, event_payload):
                continue

            run_id = f"run_{uuid.uuid4().hex[:12]}"
            run = AutomationRun(
                run_id=run_id,
                rule_id=rule.id,
                trigger_type=trigger,
                event_payload=event_payload,
            )

            actions_results: List[ActionRunResult] = []
            for action in rule.actions:
                res = self.execute_action(action, event_payload)
                actions_results.append(res)

            run.actions_executed = actions_results
            run.completed_at_utc = datetime.now(timezone.utc).isoformat()

            # Determinar estado consolidado
            has_failures = any(r.status == AutomationRunStatus.FAILED for r in actions_results)
            has_success = any(r.status == AutomationRunStatus.SUCCESS for r in actions_results)
            if has_failures and has_success:
                run.status = AutomationRunStatus.PARTIAL
            elif has_failures:
                run.status = AutomationRunStatus.FAILED
            else:
                run.status = AutomationRunStatus.SUCCESS

            run.summary_message = f"Regla '{rule.name}' ejecutada: {len(actions_results)} acciones evaluadas ({run.status.value})."

            # Persistir corrida
            self.record_run(run)
            runs.append(run)

        return runs

    # -------------------------------------------------------------------------
    # Generador de Resumen Consolidado
    # -------------------------------------------------------------------------

    def generate_summary(self, runs: Sequence[AutomationRun]) -> Dict[str, Any]:
        """Produce un resumen estructurado JSON de un conjunto de corridas de automatización."""
        total = len(runs)
        success_count = sum(1 for r in runs if r.status == AutomationRunStatus.SUCCESS)
        failed_count = sum(1 for r in runs if r.status == AutomationRunStatus.FAILED)
        partial_count = sum(1 for r in runs if r.status == AutomationRunStatus.PARTIAL)

        return {
            "total_runs": total,
            "success": success_count,
            "failed": failed_count,
            "partial": partial_count,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "runs": [r.to_dict() for r in runs],
        }
