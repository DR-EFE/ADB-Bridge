"""Servicios de la UI (puente hacia ui_automation / adb_manager)."""

from .adb_models import (
    BackupManifest, ConnectionAttempt, ConnectionMode, Device, DeviceState,
    OperationResult, TransferJob as TransferJobModel, TransferJobStatus,
)
from .adb_service import AdbError, AdbService
from .backup_service import BackupProgress, BackupService, CategoryDef, get_available_category_definitions
from .device_service import DeviceRecord, DeviceService, DeviceStatus
from .job_queue_service import JobItem, JobQueueService, JobStatus, JobType
from .monitor_service import UiNotificationMonitor
from .network_scan_service import NetworkScanResult, NetworkScanService, ScanProfile
from .transfer_service import RemotePath, TransferJob, TransferService, TransferStatus, TransferType
from .database import SQLiteStore, get_default_database_path
from .resumable_transfer import TransferItem, build_transfer_plan, execute_local_plan, sha256_file

from .sync_profiles import (
    ConflictResolution, SyncConflict, SyncDirection, SyncItemReport,
    SyncItemStatus, SyncProfile, SyncReport, SyncRule,
)
from .sync_service import SyncService
from .automation_rules import (
    ActionRunResult, ActionType, AutomationAction, AutomationCondition,
    AutomationRule, AutomationRun, AutomationRunStatus, TriggerType,
)
from .automation_service import AutomationService
from .file_explorer_models import (
    BulkItemResult, BulkOperationResult, BulkOperationType, ExplorerFilter,
    FileNode, FolderStatResult, SearchResult,
)
from .file_explorer_service import FileExplorerService
from .ollama_client import OllamaClient, OllamaError
from .ai_report_models import (
    AutomationSuggestion, AutomationSuggestionStatus, BackupAnalysisReport,
    BackupFinding, BackupRecommendation, DaySummary,
)
from .ai_summary_service import AiSummaryService
from .backup_analyzer_service import BackupAnalyzerService
from .automation_suggestion_service import AutomationSuggestionService

__all__ = [
    "Device",
    "DeviceState",
    "ConnectionAttempt",
    "ConnectionMode",
    "OperationResult",
    "BackupManifest",
    "TransferJobModel",
    "TransferJobStatus",
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
    "SQLiteStore",
    "get_default_database_path",
    "TransferItem",
    "build_transfer_plan",
    "execute_local_plan",
    "sha256_file",
    "RemotePath",
    "BackupService",
    "CategoryDef",
    "BackupProgress",
    "get_available_category_definitions",
    "JobQueueService",
    "JobItem",
    "JobStatus",
    "JobType",
    "SyncService",
    "SyncProfile",
    "SyncRule",
    "SyncDirection",
    "ConflictResolution",
    "SyncConflict",
    "SyncReport",
    "SyncItemReport",
    "SyncItemStatus",
    "AutomationService",
    "AutomationRule",
    "AutomationCondition",
    "AutomationAction",
    "AutomationRun",
    "AutomationRunStatus",
    "ActionRunResult",
    "TriggerType",
    "ActionType",
    "FileExplorerService",
    "FileNode",
    "ExplorerFilter",
    "SearchResult",
    "BulkOperationType",
    "BulkItemResult",
    "BulkOperationResult",
    "FolderStatResult",
    "OllamaClient",
    "OllamaError",
    "DaySummary",
    "BackupFinding",
    "BackupRecommendation",
    "BackupAnalysisReport",
    "AutomationSuggestionStatus",
    "AutomationSuggestion",
    "AiSummaryService",
    "BackupAnalyzerService",
    "AutomationSuggestionService",
]

