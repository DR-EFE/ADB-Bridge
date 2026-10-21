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
