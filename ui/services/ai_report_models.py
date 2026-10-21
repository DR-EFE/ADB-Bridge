"""Modelos Pydantic para reportes, análisis y sugerencias de IA (Fase B).

Define los contratos tipados para:
- DaySummary: Resumen diario de notificaciones (conteos, OTPs, alertas, viñetas).
- BackupAnalysisReport: Diagnóstico agregado de fallos en respaldos y recomendaciones.
- AutomationSuggestion: Propuesta segura de regla de automatización con confirmación humana obligatoria.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DaySummary(BaseModel):
    """Resumen estructurado de las notificaciones de una fecha determinada."""
    date: str = Field(..., description="Fecha analizada en formato YYYY-MM-DD.")
    total_notifications: int = Field(default=0, ge=0, description="Total de notificaciones procesadas.")
    app_counts: Dict[str, int] = Field(default_factory=dict, description="Distribución de notificaciones por paquete de app.")
    urgent_count: int = Field(default=0, ge=0, description="Cantidad de notificaciones de alerta crítica o bancaria.")
    otp_count: int = Field(default=0, ge=0, description="Cantidad de códigos de verificación OTP detectados.")
    urgent_and_otp_items: List[Dict[str, Any]] = Field(default_factory=list, description="Lista de notificaciones urgentes y OTPs.")
    highlights: List[str] = Field(default_factory=list, description="Viñetas resumen en español generadas por LLM o extractivas.")
    generated_by_llm: bool = Field(default=False, description="True si las viñetas fueron redactadas por Ollama, False si fue fallback.")
    model_used: Optional[str] = Field(default=None, description="Nombre del modelo Ollama empleado si aplica.")
    generated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Estampa de tiempo UTC de generación del resumen."
    )


class BackupFinding(BaseModel):
    """Hallazgo o fallo detectado durante el análisis de respaldos."""
    category: str = Field(..., description="Categoría de respaldo afectada (ej: multimedia, sms, whatsapp).")
    severity: str = Field(default="WARNING", description="Nivel de severidad: INFO, WARNING o ERROR.")
    error_code: Optional[str] = Field(default=None, description="Código de estado o error (ej: DENIED, PARTIAL, ERROR).")
    path_or_query: str = Field(..., description="Ruta o URI de consulta que experimentó el fallo.")
    failure_count: int = Field(default=1, ge=0, description="Número de veces que falló este elemento entre los respaldos analizados.")
    description: str = Field(..., description="Explicación detallada del fallo y su contexto.")


class BackupRecommendation(BaseModel):
    """Recomendación accionable ante fallos detectados en respaldos."""
    id: str = Field(..., description="Identificador único de la recomendación.")
    category: str = Field(..., description="Categoría a la que aplica la recomendación.")
    action_type: str = Field(..., description="Tipo de acción sugerida (ej: exclude_path, check_permission, manual_export).")
    recommendation: str = Field(..., description="Texto descriptivo de la sugerencia o alternativa recomendada.")
    suggested_action: Optional[Dict[str, Any]] = Field(default=None, description="Parámetros estructurados opcionales para aplicar la sugerencia.")


class BackupAnalysisReport(BaseModel):
    """Informe consolidado de diagnóstico sobre uno o varios respaldos."""
    analysis_id: str = Field(..., description="Identificador único del reporte de análisis.")
    analyzed_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Fecha y hora UTC del análisis."
    )
    backup_directories: List[str] = Field(default_factory=list, description="Directorios de respaldo analizados.")
    total_backups_analyzed: int = Field(default=0, ge=0)
    total_errors: int = Field(default=0, ge=0)
    errors_by_category: Dict[str, int] = Field(default_factory=dict)
    repeated_denied_paths: List[str] = Field(default_factory=list, description="Rutas con fallos repetidos (>= umbral).")
    findings: List[BackupFinding] = Field(default_factory=list)
    recommendations: List[BackupRecommendation] = Field(default_factory=list)
    summary_text: str = Field(default="", description="Resumen narrativo en español del estado del respaldo.")
    model_used: Optional[str] = Field(default=None, description="Modelo Ollama usado para el resumen o pulido.")


class AutomationSuggestionStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class AutomationSuggestion(BaseModel):
    """Propuesta de regla de automatización que requiere confirmación explícita del usuario."""
    id: str = Field(..., description="Identificador de la sugerencia.")
    title: str = Field(..., description="Título legible de la propuesta de automatización.")
    rationale: str = Field(..., description="Justificación de por qué se propone la regla basándose en patrones observados.")
    trigger_type: str = Field(..., description="Tipo de trigger sugerido (ej: device_connected, notification_received).")
    proposed_rule: Dict[str, Any] = Field(
        ...,
        description="Estructura completa de la regla compatible con AutomationRule (name, trigger, conditions, actions)."
    )
    confirm_required: bool = Field(
        default=True,
        description="SIEMPRE True. Las reglas nunca se aplican automáticamente sin autorización expresa del usuario."
    )
    status: AutomationSuggestionStatus = Field(default=AutomationSuggestionStatus.PENDING)
    signature: str = Field(..., description="Hash o firma única del patrón para evitar sugerencias duplicadas durante el cooldown.")
    created_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    dismissed_until_utc: Optional[str] = Field(
        default=None,
        description="Fecha UTC hasta la cual esta sugerencia no debe volver a mostrarse tras ser descartada."
    )
