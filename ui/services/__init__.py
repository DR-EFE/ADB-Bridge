"""Servicios de la UI (puente hacia ui_automation / adb_manager)."""

from .adb_models import ConnectionAttempt, ConnectionMode, Device, DeviceState, OperationResult
from .adb_service import AdbError, AdbService
from .backup_service import BackupProgress, BackupService, CategoryDef, get_available_category_definitions
from .device_service import DeviceRecord, DeviceService, DeviceStatus
from .job_queue_service import JobItem, JobQueueService, JobStatus, JobType
from .monitor_service import UiNotificationMonitor
from .network_scan_service import NetworkScanResult, NetworkScanService, ScanProfile
from .transfer_service import RemotePath, TransferJob, TransferService, TransferStatus, TransferType

__all__ = [
    "Device",
    "DeviceState",
    "ConnectionAttempt",
    "ConnectionMode",
    "OperationResult",
    "AdbService",
    "AdbError",
    "DeviceService",
    "DeviceStatus",
    "DeviceRecord",
    "UiNotificationMonitor",
    "NetworkScanService",
    "NetworkScanResult",
    "ScanProfile",
    "TransferService",
    "TransferJob",
    "TransferStatus",
    "TransferType",
    "RemotePath",
    "BackupService",
    "CategoryDef",
    "BackupProgress",
    "get_available_category_definitions",
    "JobQueueService",
    "JobItem",
    "JobStatus",
    "JobType",
]
