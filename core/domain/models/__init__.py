"""Modelos y entidades del dominio."""
from .adb import (
    DeviceState, Device, ConnectionMode, ConnectionAttempt, OperationResult,
    TransferJobStatus, TransferJob, BackupManifest, AdbError,
)
from .ai import (
    NotificationLabel, NotificationExtract, NotificationClassification,
    DaySummary, BackupFinding, BackupRecommendation, BackupAnalysisReport,
    AutomationSuggestionStatus, AutomationSuggestion,
)
from .backup import BackupFile, BackupReport, BackupDiff
from .device_health import HealthStatus, DeviceHealthSnapshot, HealthTrend, DeviceHealthTrends
from .explorer import (
    FileNode, ExplorerFilter, SearchResult, BulkOperationType,
    BulkItemResult, BulkOperationResult, FolderStatResult,
)
from .jobs import JobStatus, JobType, JobItem, is_error_transient, TRANSIENT_ERROR_PATTERNS
from .sync import (
    SyncDirection, ConflictResolution, SyncRule, SyncProfile,
    SyncConflict, SyncItemStatus, SyncItemReport, SyncReport,
)
from .photo_metadata import (
    PhotoMetadataRecord, MetadataCluster, MetadataAnalysisSummary,
    EvidenceArtifact, EvidenceManifest, MetadataSessionResult,
)
