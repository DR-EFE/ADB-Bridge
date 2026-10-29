import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import core
    import core.domain.models
    import core.domain.rules
    import core.infrastructure
    import core.services
    import backend.deps
    import backend.main
    import ui.services

    from core.domain.models.adb import Device, DeviceState, AdbError
    from core.domain.models.jobs import JobItem, JobStatus, JobType
    from core.domain.models.backup import BackupReport
    from core.domain.models.explorer import FileNode
    from core.domain.models.device_health import DeviceHealthSnapshot
    from core.domain.models.ai import BackupAnalysisReport

    from core.domain.rules.automation_rules import AutomationRule
    from core.domain.rules.backup_profiles import get_backup_profiles
    from core.domain.rules.sync_profiles import SyncProfile, SyncRule, SyncDirection

    from core.infrastructure.adb.adb_service import AdbService
    from core.infrastructure.database.sqlite import SQLiteStore
    from core.infrastructure.network.network_scan import NetworkScanService
    from core.infrastructure.ai.ollama_client import OllamaClient

    from core.services.device_service import DeviceService
    from core.services.job_queue_service import JobQueueService
    from core.services.backup_service import BackupService
    from core.services.sync_service import SyncService
    from core.services.file_explorer_service import FileExplorerService
    from core.services.device_health_service import DeviceHealthService
    from core.services.automation_service import AutomationService

    print("SUCCESS: ALL CORE AND BACKEND IMPORTS LOADED PERFECTLY!")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
