"""Capa de Infraestructura (Drivers, BD, Red, IA, Sistema)."""
from .adb.adb_service import AdbError, AdbService
from .adb.resumable_transfer import TransferItem, build_transfer_plan, execute_local_plan, sha256_file
from .adb.wireless_qr import WirelessQrError, WirelessQrPairingService
from .database.sqlite import SQLiteStore, get_default_database_path
from .network.network_scan import NetworkScanService, ScanProfile, NetworkScanResult
from .ai.ollama_client import OllamaClient, OllamaError
from .system.monitor_service import UiNotificationMonitor
