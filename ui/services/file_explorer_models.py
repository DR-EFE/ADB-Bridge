"""Modelos tipados para el servicio de exploración de archivos avanzado (Fase 5B).

Define las entidades y contratos para:
- Representación de nodos en árbol y listado (FileNode)
- Criterios de filtrado por nombre, extensión, tamaño y fechas (ExplorerFilter)
- Resultados de búsquedas (SearchResult)
- Resultados de operaciones masivas (BulkOperationResult, BulkItemResult)
- Estadísticas de carpetas / du (FolderStatResult)
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FileNode(BaseModel):
    """Representa un archivo, directorio o enlace en el almacenamiento del dispositivo Android."""
    path: str = Field(..., description="Ruta absoluta remota en el dispositivo Android (ej: /sdcard/Download/doc.pdf).")
    name: str = Field(..., description="Nombre del archivo o directorio.")
    is_dir: bool = Field(default=False, description="True si es un directorio.")
    size: int = Field(default=0, ge=0, description="Tamaño en bytes.")
    mtime: Optional[datetime] = Field(default=None, description="Fecha y hora de última modificación.")
    is_link: bool = Field(default=False, description="True si es un enlace simbólico.")
    link_target: Optional[str] = Field(default=None, description="Destino del symlink si aplica.")
    permissions: Optional[str] = Field(default=None, description="Permisos UNIX (ej: drwxrwxr-x).")
    children: Optional[List[FileNode]] = Field(default=None, description="Hijos en caso de representación en árbol.")

    @property
    def extension(self) -> str:
        """Devuelve la extensión en minúsculas incluyendo el punto (ej: '.jpg') o cadena vacía si es directorio."""
        if self.is_dir:
            return ""
        if "." in self.name and not self.name.startswith("."):
            return "." + self.name.rsplit(".", 1)[-1].lower()
        return ""

    @property
    def formatted_size(self) -> str:
        """Devuelve el tamaño formateado para visualización amigable en la interfaz."""
        if self.is_dir:
            return "<DIR>"
        b = self.size
        if b >= 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"
        if b >= 1024 * 1024:
            return f"{b / (1024 * 1024):.2f} MB"
        if b >= 1024:
            return f"{b / 1024:.2f} KB"
        return f"{b} B"


class ExplorerFilter(BaseModel):
    """Filtros configurables para búsqueda y listado en el explorador."""
    name_glob: Optional[str] = Field(default=None, description="Patrón glob para nombre (ej: *.jpg, backup_*, report.pdf).")
    extensions: Optional[List[str]] = Field(default=None, description="Lista de extensiones permitidas (ej: ['.jpg', '.png'] o ['pdf']).")
    min_size: Optional[int] = Field(default=None, ge=0, description="Tamaño mínimo en bytes.")
    max_size: Optional[int] = Field(default=None, ge=0, description="Tamaño máximo en bytes.")
    modified_after: Optional[datetime] = Field(default=None, description="Archivos modificados después de esta fecha.")
    modified_before: Optional[datetime] = Field(default=None, description="Archivos modificados antes de esta fecha.")
    only_dirs: bool = Field(default=False, description="Si es True, sólo incluye directorios.")
    only_files: bool = Field(default=False, description="Si es True, sólo incluye archivos.")

    def matches(self, node: FileNode) -> bool:
        """Determina si un nodo de archivo cumple todos los criterios definidos."""
        if self.only_dirs and not node.is_dir:
            return False
        if self.only_files and node.is_dir:
            return False

        # Validación por nombre / glob
        if self.name_glob:
            if not fnmatch.fnmatch(node.name.lower(), self.name_glob.lower()):
                return False

        # Validación por extensión
        if self.extensions and not node.is_dir:
            normalized_exts = [
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in self.extensions
            ]
            if node.extension.lower() not in normalized_exts:
                return False

        # Validación por tamaño
        if not node.is_dir:
            if self.min_size is not None and node.size < self.min_size:
                return False
            if self.max_size is not None and node.size > self.max_size:
                return False

        # Validación por fecha de modificación
        if node.mtime:
            if self.modified_after and node.mtime < self.modified_after:
                return False
            if self.modified_before and node.mtime > self.modified_before:
                return False

        return True


class SearchResult(BaseModel):
    """Resultado estructurado de una operación de búsqueda de archivos."""
    root: str = Field(..., description="Ruta raíz donde se realizó la búsqueda.")
    total_found: int = Field(default=0, description="Cantidad total de coincidencias encontradas.")
    items: List[FileNode] = Field(default_factory=list, description="Lista de nodos encontrados.")
    truncated: bool = Field(default=False, description="True si se alcanzó el límite máximo de resultados.")
    duration_seconds: float = Field(default=0.0, description="Tiempo de ejecución en segundos.")


class BulkOperationType(str, Enum):
    DOWNLOAD = "download"
    UPLOAD = "upload"
    DELETE = "delete"
    TRASH = "trash"


class BulkItemResult(BaseModel):
    """Resultado individual de un elemento en una operación masiva."""
    source: str = Field(..., description="Ruta de origen (local o remota según operación).")
    destination: Optional[str] = Field(default=None, description="Ruta de destino si aplica.")
    success: bool = Field(..., description="True si la acción sobre el elemento fue exitosa.")
    error: Optional[str] = Field(default=None, description="Mensaje de error si la acción falló.")
    bytes_transferred: int = Field(default=0, description="Bytes transferidos si aplica.")


class BulkOperationResult(BaseModel):
    """Resultado general de una operación masiva sobre múltiples archivos."""
    operation: BulkOperationType = Field(..., description="Tipo de operación masiva realizada.")
    serial: str = Field(..., description="Serial explícito del dispositivo.")
    total_items: int = Field(default=0, description="Total de elementos procesados o encolados.")
    succeeded_items: int = Field(default=0, description="Total de elementos procesados con éxito.")
    failed_items: int = Field(default=0, description="Total de elementos fallidos.")
    results: List[BulkItemResult] = Field(default_factory=list, description="Detalle por elemento.")
    job_id: Optional[str] = Field(default=None, description="ID del trabajo encolado en JobQueueService si aplica.")
    duration_seconds: float = Field(default=0.0, description="Duración total en segundos.")
    message: Optional[str] = Field(default=None, description="Mensaje contextual o resumen.")


class FolderStatResult(BaseModel):
    """Estadística de almacenamiento o cálculo de tamaño de una carpeta remota."""
    path: str = Field(..., description="Ruta remota analizada.")
    supported: bool = Field(default=True, description="True si el dispositivo soporta el comando (du/stat).")
    total_size_bytes: Optional[int] = Field(default=None, description="Tamaño total aproximado en bytes.")
    raw_output: Optional[str] = Field(default=None, description="Salida pura del comando shell.")
    message: Optional[str] = Field(default=None, description="Mensaje explicativo o estado de compatibilidad.")
