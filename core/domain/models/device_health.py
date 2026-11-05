"""Modelos serializables para salud y tendencias de un dispositivo Android."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4


class HealthStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    ERROR = "ERROR"


@dataclass
class DeviceHealthSnapshot:
    serial: str
    snapshot_id: str = field(default_factory=lambda: uuid4().hex)
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    overall_status: str = HealthStatus.PARTIAL.value
    connection_type: Optional[str] = None
    battery_level: Optional[int] = None
    battery_status: Optional[str] = None
    temperature_celsius: Optional[float] = None
    storage_total_bytes: Optional[int] = None
    storage_used_bytes: Optional[int] = None
    storage_free_bytes: Optional[int] = None
    ram_total_bytes: Optional[int] = None
    ram_available_bytes: Optional[int] = None
    android_version: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    network_status: Optional[str] = None
    adb_latency_ms: Optional[float] = None
    processes_status: str = HealthStatus.UNSUPPORTED.value
    field_status: Dict[str, str] = field(default_factory=dict)
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, object]) -> "DeviceHealthSnapshot":
        return cls(**value)  # type: ignore[arg-type]


@dataclass
class HealthTrend:
    metric: str
    samples: int
    latest: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]
    average: Optional[float]


@dataclass
class DeviceHealthTrends:
    serial: str
    days: int
    trends: Dict[str, HealthTrend] = field(default_factory=dict)
