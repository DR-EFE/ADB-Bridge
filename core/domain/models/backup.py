"""Modelos serializables específicos del Centro de respaldos."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


@dataclass
class BackupFile:
    path: str
    sha256: str
    size: int
    category: str
    remote_path: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class BackupReport:
    backup_id: str
    serial: str
    profile_id: str
    destination: str
    status: str
    categories: List[Dict[str, object]] = field(default_factory=list)
    files: List[BackupFile] = field(default_factory=list)
    errors: List[Dict[str, object]] = field(default_factory=list)
    generated_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, object]:
        return {
            "SchemaVersion": 2, "BackupId": self.backup_id, "DeviceSerial": self.serial,
            "Profile": self.profile_id, "Destination": self.destination, "OverallStatus": self.status,
            "GeneratedUtc": self.generated_utc, "Categories": self.categories,
            "Files": [item.to_dict() for item in self.files], "Errors": self.errors,
        }


@dataclass
class BackupDiff:
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    duplicates: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
