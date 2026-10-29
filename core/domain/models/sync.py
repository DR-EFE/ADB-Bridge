"""Modelos Pydantic para el subsistema de sincronización inteligente (Fase 3B).

Define perfiles de sincronización, reglas, estados, detección de conflictos
y reportes de ejecución estructurados en formato JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SyncDirection(str, Enum):
    """Dirección de sincronización entre el dispositivo y la PC."""
    PHONE_TO_PC = "phone_to_pc"
    PC_TO_PHONE = "pc_to_phone"
    BIDIRECTIONAL = "bidirectional"


class ConflictResolution(str, Enum):
    """Estrategia de resolución ante colisiones de archivos."""
    KEEP_LOCAL = "keep_local"       # Prevalece archivo en PC
    KEEP_REMOTE = "keep_remote"     # Prevalece archivo en teléfono
    KEEP_BOTH = "keep_both"         # Conserva ambos renombrando el conflicto
    SKIP = "skip"                   # No toca ninguno y lo anota en el reporte


class SyncRule(BaseModel):
    """Reglas configurables por perfil de sincronización."""
    exclude_patterns: List[str] = Field(
        default_factory=lambda: [".*", "*.tmp", "*.part", "Thumbs.db", ".nomedia"],
        description="Patrones glob a excluir (ej. .trashed-*, *.tmp)."
    )
    exclude_dirs: List[str] = Field(
        default_factory=lambda: [".conflict", ".trash", ".thumbnails", "cache", "Cache"],
        description="Nombres de subdirectorios a excluir recursivamente."
    )
    max_file_size_bytes: Optional[int] = Field(
        default=None,
        ge=0,
        description="Límite máximo de tamaño por archivo en bytes (None = ilimitado)."
    )
    max_total_bytes: Optional[int] = Field(
        default=None,
        ge=0,
        description="Límite máximo total transferible en la sesión (None = ilimitado)."
    )
    preserve_structure: bool = Field(
        default=True,
        description="Conserva la jerarquía relativa de subdirectorios."
    )
    use_trash: bool = Field(
        default=True,
        description="Mueve archivos reemplazados o en conflicto a carpeta .trash/.conflict en vez de destruirlos."
    )
    compress: bool = Field(
        default=False,
        description="Indica si los elementos se empaquetan o procesan con compresión cuando aplique."
    )
    conflict_strategy: ConflictResolution = Field(
        default=ConflictResolution.KEEP_BOTH,
        description="Estrategia predeterminada al detectar colisión de contenido."
    )
    only_new_photos: bool = Field(
        default=False,
        description="Filtra exclusivamente formatos de imagen/video (jpg, png, mp4, heic, dng, webp)."
    )
    only_modified_docs: bool = Field(
        default=False,
        description="Filtra exclusivamente documentos (pdf, doc, docx, txt, xls, xlsx, pptx, epub)."
    )


class SyncProfile(BaseModel):
    """Definición y configuración de un perfil de sincronización."""
    id: str = Field(..., min_length=1, description="Identificador único del perfil (ej. photos_to_pc).")
    name: str = Field(..., min_length=1, description="Nombre legible del perfil.")
    direction: SyncDirection = Field(..., description="Dirección de transferencia.")
    remote_path: str = Field(..., min_length=1, description="Ruta base en el teléfono (ej. /sdcard/DCIM).")
    local_path: str = Field(..., min_length=1, description="Ruta base en la PC.")
    serial: Optional[str] = Field(default=None, description="Serial específico o None para dispositivo activo.")
    rules: SyncRule = Field(default_factory=SyncRule, description="Reglas asociadas al perfil.")
    enabled: bool = Field(default=True, description="Indica si el perfil está habilitado para ejecución.")
    last_sync_utc: Optional[str] = Field(default=None, description="Timestamp ISO del último sync exitoso.")
    created_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Fecha de creación del perfil."
    )


class SyncConflict(BaseModel):
    """Representa un conflicto detectado entre dos extremos."""
    relative_path: str = Field(..., description="Ruta relativa del archivo en colisión.")
    local_size: int = Field(default=0, ge=0)
    remote_size: int = Field(default=0, ge=0)
    local_sha256: Optional[str] = Field(default=None)
    remote_sha256: Optional[str] = Field(default=None)
    resolution: ConflictResolution = Field(default=ConflictResolution.SKIP)
    action_taken: str = Field(default="", description="Detalle de la acción ejecutada (ej. Renombrado a ...).")


class SyncItemStatus(str, Enum):
    TRANSFERRED = "transferred"
    SKIPPED_SAME = "skipped_same"
    SKIPPED_RULE = "skipped_rule"
    CONFLICT = "conflict"
    FAILED = "failed"


class SyncItemReport(BaseModel):
    """Reporte individual por archivo evaluado."""
    path: str
    status: SyncItemStatus
    size_bytes: int = 0
    sha256: Optional[str] = None
    reason: Optional[str] = None


class SyncReport(BaseModel):
    """Reporte estructurado final de la ejecución de una sincronización."""
    sync_id: str = Field(..., description="Identificador del sync ejecutado.")
    profile_id: str = Field(..., description="ID del perfil sincronizado.")
    direction: SyncDirection
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    success: bool = True
    transferred_count: int = 0
    transferred_bytes: int = 0
    skipped_count: int = 0
    skipped_bytes: int = 0
    conflict_count: int = 0
    failed_count: int = 0
    conflicts: List[SyncConflict] = Field(default_factory=list)
    items: List[SyncItemReport] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
