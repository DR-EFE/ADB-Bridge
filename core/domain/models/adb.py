"""Modelos Pydantic tipados para el contrato unificado de ADB (Fase 1).

Estos modelos representan las entidades mínimas para interactuar con dispositivos
Android a través de ADB, alineados con el contrato del módulo PowerShell AndroidAdb.psm1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class DeviceState(str, Enum):
    """Estados estándar reportados por ADB."""
    DEVICE = "device"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    AUTHORIZING = "authorizing"
    RECOVERY = "recovery"
    SIDELOAD = "sideload"
    BOOTLOADER = "bootloader"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> DeviceState:
        return cls.UNKNOWN


class Device(BaseModel):
    """Representación tipada de un dispositivo Android."""
    serial: str = Field(..., description="Serial ADB o Endpoint IP:Port del dispositivo.")
    state: DeviceState = Field(default=DeviceState.DEVICE, description="Estado ADB del dispositivo.")
    is_network: bool = Field(default=False, description="True si la conexión es vía red (IP:Puerto).")
    friendly_name: Optional[str] = Field(default=None, description="Nombre descriptivo asignado o alias.")
    model: Optional[str] = Field(default=None, description="Modelo de hardware (ro.product.model).")
    battery_level: Optional[int] = Field(default=None, ge=0, le=100, description="Porcentaje de batería.")
    android_version: Optional[str] = Field(default=None, description="Versión de Android (ro.build.version.release).")
    usb_serial: Optional[str] = Field(default=None, description="Serial USB asociado si aplica.")
    physical_id_hash: Optional[str] = Field(default=None, description="Hash SHA256 para verificación de identidad física.")
    properties: Dict[str, str] = Field(default_factory=dict, description="Propiedades getprop en caché.")

    @field_validator("is_network", mode="before")
    @classmethod
    def compute_is_network(cls, v: Any) -> bool:
        return bool(v)

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Device:
        # Pre-calcula is_network si no se pasó explícitamente
        if isinstance(obj, dict):
            serial = obj.get("serial", "")
            if "is_network" not in obj:
                obj["is_network"] = bool(":" in str(serial) and str(serial).rsplit(":", 1)[-1].isdigit())
        return super().model_validate(obj, *args, **kwargs)

    def __init__(self, **data: Any):
        if "is_network" not in data and "serial" in data:
            serial = str(data["serial"])
            data["is_network"] = bool(":" in serial and serial.rsplit(":", 1)[-1].isdigit())
        super().__init__(**data)

    @property
    def is_ready(self) -> bool:
        """Determina si el dispositivo está en estado 'device' listo para operaciones."""
        return self.state == DeviceState.DEVICE


class ConnectionMode(str, Enum):
    """Modo de conexión ADB."""
    TCPIP = "tcpip-5555"
    WIRELESS_TLS = "wireless-debugging-tls"
    USB = "usb"
    UNKNOWN = "unknown"


class ConnectionAttempt(BaseModel):
    """Registro tipado de un intento de emparejamiento o conexión a un endpoint."""
    endpoint: str = Field(..., description="Host y puerto objetivo (ej: 192.168.1.100:5555 o [::1]:38899).")
    host: str = Field(..., description="IP o hostname del destino.")
    port: int = Field(..., ge=1, le=65535, description="Puerto numérico.")
    mode: ConnectionMode = Field(default=ConnectionMode.TCPIP, description="Modo de conexión empleado.")
    success: bool = Field(..., description="True si la conexión culminó exitosamente en estado device.")
    message: str = Field(default="", description="Mensaje explicativo o de error.")
    exit_code: int = Field(default=0, description="Código de salida del proceso ADB.")
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Fecha y hora UTC del intento.")
    physical_id_hash: Optional[str] = Field(default=None, description="Hash SHA256 del ID físico verificado.")


class OperationResult(BaseModel):
    """Resultado estructurado de una ejecución de comando ADB."""
    success: bool = Field(..., description="True si el comando finalizó con código 0 y sin error fatal.")
    exit_code: int = Field(..., description="Código de salida del proceso ejecutado.")
    stdout: str = Field(default="", description="Salida estándar del comando.")
    stderr: str = Field(default="", description="Salida de error del comando.")
    command: List[str] = Field(default_factory=list, description="Lista de argumentos ejecutados.")
    serial: Optional[str] = Field(default=None, description="Serial explícito al que se dirigió la orden (-s).")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Duración de la ejecución en segundos.")
    message: Optional[str] = Field(default=None, description="Mensaje contextual adicional o resumen del resultado.")

    @property
    def output(self) -> str:
        """Devuelve stdout o stderr de manera combinada y limpia."""
        out = self.stdout.strip()
        err = self.stderr.strip()
        if out and err:
            return f"{out}\n{err}"
        return out or err


class TransferJobStatus(str, Enum):
    """Estados persistibles de una transferencia reanudable."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransferJob(BaseModel):
    """Contrato persistible para trabajos de transferencia.

    No contiene objetos de runtime como ``threading.Event`` o ``Popen``; esos
    detalles pertenecen al worker. El modelo puede serializarse directamente a
    JSON y reconstruirse después de reiniciar la aplicación.
    """

    id: str = Field(..., min_length=1)
    serial: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    operation: str = Field(default="transfer", min_length=1)
    status: TransferJobStatus = TransferJobStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    bytes_total: int = Field(default=0, ge=0)
    bytes_completed: int = Field(default=0, ge=0)
    files_total: int = Field(default=0, ge=0)
    files_completed: int = Field(default=0, ge=0)
    current_file: Optional[str] = None
    attempts: int = Field(default=0, ge=0)
    max_retries: int = Field(default=2, ge=0)
    skip_existing: bool = False
    only_changes: bool = False
    checksums: Dict[str, str] = Field(default_factory=dict)
    failed_files: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def mark_progress(self, *, bytes_completed: int, files_completed: int,
                      current_file: Optional[str] = None) -> None:
        self.bytes_completed = max(0, bytes_completed)
        self.files_completed = max(0, files_completed)
        self.progress = (
            min(1.0, self.bytes_completed / self.bytes_total)
            if self.bytes_total else
            min(1.0, self.files_completed / self.files_total)
            if self.files_total else 0.0
        )
        self.current_file = current_file
        self.updated_at = datetime.now(timezone.utc)

    def complete(self) -> None:
        self.status = TransferJobStatus.COMPLETED
        self.progress = 1.0
        self.bytes_completed = self.bytes_total
        self.files_completed = self.files_total
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = self.completed_at

    def fail(self, message: str) -> None:
        self.status = TransferJobStatus.FAILED
        self.error = message
        self.updated_at = datetime.now(timezone.utc)


class BackupManifest(BaseModel):
    """Metadatos y resultado verificable de un backup."""

    id: str = Field(..., min_length=1)
    serial: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    status: TransferJobStatus = TransferJobStatus.PENDING
    categories: Dict[str, str] = Field(default_factory=dict)
    files: List[str] = Field(default_factory=list)
    checksums: Dict[str, str] = Field(default_factory=dict)
    total_bytes: int = Field(default=0, ge=0)
    completed_bytes: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @property
    def complete(self) -> bool:
        return self.status == TransferJobStatus.COMPLETED and self.error_count == 0


class AdbError(RuntimeError):
    """Excepción base para fallos del contrato ADB con código de error normalizado."""
    def __init__(self, code: str, message: str, operation_result: Optional[OperationResult] = None):
        self.code = code
        self.message = message
        self.result = operation_result
        super().__init__(f"[{code}] {message}")
