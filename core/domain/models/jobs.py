"""Modelos de dominio para Trabajos y Tareas (Jobs)."""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

TRANSIENT_ERROR_PATTERNS = [
    r"device offline",
    r"device.*not found",
    r"connection reset",
    r"connection refused",
    r"broken pipe",
    r"protocol fault",
    r"closed",
    r"timeout",
    r"killed",
    r"no devices/emulators found",
    r"temporarily unavailable",
    r"resource temporarily unavailable",
]


def is_error_transient(error_message: str) -> bool:
    """Evalúa si un mensaje de error corresponde a un fallo transitorio reintentable."""
    msg = (error_message or "").lower()
    return any(re.search(pat, msg) for pat in TRANSIENT_ERROR_PATTERNS)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    WHATSAPP_EXPORT_CHAT = "whatsapp_export_chat"
    WHATSAPP_PROCESS = "whatsapp_process"
    BACKUP_CATEGORY = "backup_category"
    FILE_TRANSFER = "file_transfer"
    ADB_COMMAND = "adb_command"
    LOCAL_PROCESS = "local_process"


@dataclass
class JobItem:
    id: str
    job_type: JobType
    title: str
    serial: Optional[str]
    status: JobStatus = JobStatus.PENDING
    progress_percent: float = 0.0
    current_step: str = ""
    error: Optional[str] = None
    retries_left: int = 5
    max_retries: int = 5
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    pause_event: threading.Event = field(default_factory=threading.Event, repr=False)
    bytes_total: int = 0
    bytes_completed: int = 0
    files_total: int = 0
    files_completed: int = 0
    skip_existing: bool = False
    only_changes: bool = False
    checksums: Dict[str, str] = field(default_factory=dict)
    failed_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "job_type": self.job_type.value if isinstance(self.job_type, JobType) else str(self.job_type),
            "title": self.title,
            "serial": self.serial,
            "status": self.status.value if isinstance(self.status, JobStatus) else str(self.status),
            "progress_percent": self.progress_percent,
            "current_step": self.current_step,
            "error": self.error,
            "retries_left": self.retries_left,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "payload": self.payload,
            "result": self.result,
            "bytes_total": self.bytes_total,
            "bytes_completed": self.bytes_completed,
            "files_total": self.files_total,
            "files_completed": self.files_completed,
            "skip_existing": self.skip_existing,
            "only_changes": self.only_changes,
            "checksums": self.checksums,
            "failed_files": self.failed_files,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JobItem:
        raw_type = data.get("job_type", JobType.LOCAL_PROCESS.value)
        try:
            job_type = JobType(raw_type)
        except ValueError:
            job_type = JobType.LOCAL_PROCESS

        raw_status = data.get("status", JobStatus.PENDING.value)
        try:
            status = JobStatus(raw_status)
        except ValueError:
            status = JobStatus.PENDING

        item = cls(
            id=data.get("id", f"job_{uuid.uuid4().hex[:12]}"),
            job_type=job_type,
            title=data.get("title", "Trabajo sin título"),
            serial=data.get("serial"),
            status=status,
            progress_percent=float(data.get("progress_percent", 0.0)),
            current_step=data.get("current_step", ""),
            error=data.get("error"),
            retries_left=int(data.get("retries_left", 5)),
            max_retries=int(data.get("max_retries", 5)),
            created_at=data.get("created_at", datetime.now().isoformat()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            payload=data.get("payload", {}),
            result=data.get("result"),
            bytes_total=int(data.get("bytes_total", 0)),
            bytes_completed=int(data.get("bytes_completed", 0)),
            files_total=int(data.get("files_total", 0)),
            files_completed=int(data.get("files_completed", 0)),
            skip_existing=bool(data.get("skip_existing", False)),
            only_changes=bool(data.get("only_changes", False)),
            checksums=dict(data.get("checksums", {})),
            failed_files=list(data.get("failed_files", [])),
        )
        return item
