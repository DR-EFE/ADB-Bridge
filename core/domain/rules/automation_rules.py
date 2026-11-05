"""Modelos Pydantic para el subsistema de automatizaciones por reglas (Fase 4B).

Define triggers, condiciones, acciones, reglas y reportes de ejecuciones
para reaccionar automáticamente ante eventos (conexión de dispositivo,
notificaciones entrantes, baja capacidad de almacenamiento, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TriggerType(str, Enum):
    """Tipos de disparadores admitidos por el motor de automatización."""
    DEVICE_CONNECTED = "device_connected"
    NOTIFICATION_RECEIVED = "notification_received"
    STORAGE_LOW = "storage_low"
    SCHEDULE = "schedule"


class ActionType(str, Enum):
    """Acciones permitidas y controladas por el motor."""
    RUN_BACKUP_PROFILE = "run_backup_profile"
    RUN_SYNC_PROFILE = "run_sync_profile"
    SAVE_NOTIFICATION = "save_notification"
    LOG_SUMMARY = "log_summary"
    SHELL_SAFE_COMMAND = "shell_safe_command"


class AutomationCondition(BaseModel):
    """Condiciones que deben cumplirse para disparar la acción."""
    serial: Optional[str] = Field(
        default=None,
        description="Serial específico del dispositivo o None para aplicar a cualquiera."
    )
    package_name: Optional[str] = Field(
        default=None,
        description="Filtro exacto o comodín del paquete de la app de notificación (ej. com.whatsapp)."
    )
    text_contains: Optional[str] = Field(
        default=None,
        description="Subcadena que debe estar presente en el título, texto o payload del evento (insensible a mayúsculas)."
    )
    profile_id: Optional[str] = Field(
        default=None,
        description="ID del perfil de backup o sync asociado si aplica."
    )
    storage_threshold_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Porcentaje de almacenamiento libre por debajo del cual se dispara STORAGE_LOW."
    )


class AutomationAction(BaseModel):
    """Definición de una acción individual a ejecutar."""
    action_type: ActionType
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parámetros de la acción (ej. {'profile_id': 'photos_to_pc'}, {'command': 'uptime'})."
    )


class AutomationRule(BaseModel):
    """Regla completa de automatización."""
    id: str = Field(..., min_length=1, description="Identificador único de la regla.")
    name: str = Field(..., min_length=1, description="Nombre descriptivo de la regla.")
    trigger: TriggerType
    conditions: AutomationCondition = Field(default_factory=AutomationCondition)
    actions: List[AutomationAction] = Field(default_factory=list)
    enabled: bool = Field(default=True)
    created_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AutomationRunStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class ActionRunResult(BaseModel):
    """Resultado individual de una acción ejecutada."""
    action_type: ActionType
    status: AutomationRunStatus
    job_id: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None


class AutomationRun(BaseModel):
    """Registro histórico de ejecución de una regla."""
    run_id: str = Field(..., description="ID de la corrida de automatización.")
    rule_id: str = Field(..., description="ID de la regla disparada.")
    trigger_type: TriggerType
    event_payload: Dict[str, Any] = Field(default_factory=dict)
    status: AutomationRunStatus = AutomationRunStatus.SUCCESS
    actions_executed: List[ActionRunResult] = Field(default_factory=list)
    started_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at_utc: Optional[str] = None
    summary_message: str = Field(default="")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
