"""Servicio de estado y administración visual de dispositivos para la UI.

Registro canónico: %USERPROFILE%\\AndroidTools\\devices.json
Migración automática desde config/devices.json (solo una vez si no existe el canónico).

Funcionalidades:
1. Modelo ampliado con campos opcionales (Endpoints hasta 10, metadatos, perfiles de red, etc.).
2. Deduplicación por UsbSerial -> MdnsServiceName -> LastEndpoint -> Advertencia manual.
3. Selección segura: 1 dispositivo => auto-selección; múltiples dispositivos => selección obligatoria.
4. Acciones completas: connect, disconnect, set_primary, rename, remove, refresh_metadata.
5. Supervisión, reconexión inteligente y compatibilidad con endpoints privados / VPN.
"""

from __future__ import annotations

import json
import hashlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import adbutils

from ui_automation.adb_manager import (
    ensure_single_adb_server,
    get_adb_client,
    get_adb_executable,
    is_network_serial,
)

logger = logging.getLogger("ui.device_service")
DEVICE_STORE_LOCK = threading.RLock()


def get_canonical_devices_json_path() -> Path:
    """Obtiene la ruta canónica del registro de dispositivos."""
    userprofile = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(userprofile) / "AndroidTools" / "devices.json"


CANONICAL_DEVICES_JSON = get_canonical_devices_json_path()
LEGACY_DEVICES_JSON = Path(__file__).resolve().parents[2] / "config" / "devices.json"
DEVICES_JSON = CANONICAL_DEVICES_JSON


def ensure_devices_store_migrated(
    canonical_path: Path = CANONICAL_DEVICES_JSON,
    legacy_path: Path = LEGACY_DEVICES_JSON,
) -> None:
    """Migra automáticamente una sola vez desde config/devices.json si el canónico no existe."""
    if not canonical_path.is_file() and legacy_path.is_file():
        try:
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            content = legacy_path.read_text(encoding="utf-8")
            canonical_path.write_text(content, encoding="utf-8")
            logger.info("Migrado automáticamente config/devices.json a %s", canonical_path)
        except Exception as exc:
            logger.warning("No se pudo migrar config/devices.json: %s", exc)


@dataclass
class RetryPolicy:
    """Configuración de reintentos exponenciales para la reconexión."""

    initial_interval: float = 2.0
    max_interval: float = 30.0
    backoff_factor: float = 2.0
    timeout: float = 120.0


@dataclass
class WatchHandle:
    """Control para detener un hilo de supervisión en segundo plano."""

    stop_event: threading.Event
    thread: threading.Thread

    def stop(self) -> None:
        self.stop_event.set()


@dataclass
class EndpointRecord:
    """Registro de endpoint exitoso."""

    endpoint: str
    connected_utc: str
    mdns_service_name: Optional[str] = None
    source: Optional[str] = None
    status: str = "device"
    network_bssid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Endpoint": self.endpoint,
            "ConnectedUtc": self.connected_utc,
            "MdnsServiceName": self.mdns_service_name,
            "Source": self.source,
            "Status": self.status,
            "NetworkBssid": self.network_bssid,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EndpointRecord:
        return cls(
            endpoint=data.get("Endpoint") or data.get("endpoint") or "",
            connected_utc=data.get("ConnectedUtc") or data.get("connected_utc") or "",
            mdns_service_name=data.get("MdnsServiceName") or data.get("mdns_service_name"),
            source=data.get("Source") or data.get("source"),
            status=data.get("Status") or data.get("status") or "device",
            network_bssid=data.get("NetworkBssid") or data.get("network_bssid"),
        )


@dataclass
class DeviceRecord:
    """Registro canónico de dispositivo Android ampliado."""

    id: str
    friendly_name: str
    usb_serial: Optional[str] = None
    wifi_mac_address: Optional[str] = None
    last_endpoint: Optional[str] = None
    mode: Optional[str] = None
    mdns_service_name: Optional[str] = None
    last_ip: Optional[str] = None
    last_connected_utc: Optional[str] = None
    last_seen_utc: Optional[str] = None
    model: Optional[str] = None
    android_version: Optional[str] = None
    battery: Optional[int] = None
    is_paired: bool = False
    endpoints: List[Dict[str, Any]] = field(default_factory=list)
    network_profiles: List[Dict[str, Any]] = field(default_factory=list)
    physical_id_hash: Optional[str] = None
    last_successful_reconnect_method: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Id": self.id,
            "FriendlyName": self.friendly_name,
            "UsbSerial": self.usb_serial,
            "WifiMacAddress": self.wifi_mac_address,
            "LastEndpoint": self.last_endpoint,
            "Mode": self.mode,
            "MdnsServiceName": self.mdns_service_name,
            "LastIp": self.last_ip,
            "LastConnectedUtc": self.last_connected_utc,
            "LastSeenUtc": self.last_seen_utc,
            "Model": self.model,
            "AndroidVersion": self.android_version,
            "Battery": self.battery,
            "IsPaired": self.is_paired,
            "Endpoints": self.endpoints[:10],
            "NetworkProfiles": self.network_profiles,
            "PhysicalIdHash": self.physical_id_hash,
            "LastSuccessfulReconnectMethod": self.last_successful_reconnect_method,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeviceRecord:
        return cls(
            id=data.get("Id") or data.get("id") or "",
            friendly_name=data.get("FriendlyName") or data.get("friendly_name") or "Android Device",
            usb_serial=data.get("UsbSerial") or data.get("usb_serial"),
            wifi_mac_address=data.get("WifiMacAddress") or data.get("wifi_mac_address"),
            last_endpoint=data.get("LastEndpoint") or data.get("last_endpoint"),
            mode=data.get("Mode") or data.get("mode"),
            mdns_service_name=data.get("MdnsServiceName") or data.get("mdns_service_name"),
            last_ip=data.get("LastIp") or data.get("last_ip"),
            last_connected_utc=data.get("LastConnectedUtc") or data.get("last_connected_utc"),
            last_seen_utc=data.get("LastSeenUtc") or data.get("last_seen_utc"),
            model=data.get("Model") or data.get("model"),
            android_version=data.get("AndroidVersion") or data.get("android_version"),
            battery=data.get("Battery") or data.get("battery"),
            is_paired=bool(data.get("IsPaired") or data.get("is_paired", False)),
            endpoints=list(data.get("Endpoints") or data.get("endpoints") or []),
            network_profiles=list(data.get("NetworkProfiles") or data.get("network_profiles") or []),
            physical_id_hash=data.get("PhysicalIdHash") or data.get("physical_id_hash"),
            last_successful_reconnect_method=data.get("LastSuccessfulReconnectMethod") or data.get("last_successful_reconnect_method"),
        )


@dataclass
class OperationResult:
    """Resultado estándar de operaciones de dispositivo."""

    success: bool
    device_id: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


@dataclass
class ConnectionResult:
    """Resultado de intento de conexión."""

    success: bool
    device_id: Optional[str] = None
    endpoint: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    method: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    provisional: bool = False


@dataclass
class DeviceInfo:
    """Información resumida y ampliada de un dispositivo ADB."""

    serial: str
    state: str  # device | unauthorized | offline | no detectado
    is_network: bool
    device_id: Optional[str] = None
    friendly_name: Optional[str] = None
    model: Optional[str] = None
    battery: Optional[int] = None
    android_version: Optional[str] = None
    mode: Optional[str] = None
    last_endpoint: Optional[str] = None
    last_connected_utc: Optional[str] = None
    last_seen_utc: Optional[str] = None
    is_primary: bool = False
    duplicate_warning: Optional[str] = None


@dataclass
class DeviceStatus:
    """Instantánea del estado del dispositivo activo."""

    serial: Optional[str] = None
    adb_state: str = "sin dispositivo"  # device | unauthorized | offline | desconectado
    connected: bool = False
    model: str = "—"
    battery: Optional[int] = None
    temperature: Optional[float] = None
    android_version: Optional[str] = None
    error: Optional[str] = None
    available_devices: List[DeviceInfo] = field(default_factory=list)
    requires_selection: bool = False
    primary_device_id: Optional[str] = None
    is_stale: bool = False


@dataclass
class TechnicalDiagnosticsReport:
    """Informe consolidado de diagnóstico técnico del sistema ADB y dispositivo."""

    adb_version: str = "Desconocida"
    adb_path: str = "No localizado"
    adb_server_status: str = "Detenido o sin respuesta"
    adb_server_port: int = 5037
    adb_server_owner: Dict[str, Any] = field(default_factory=dict)
    last_reconnect_issue: Dict[str, Any] = field(default_factory=dict)
    detected_devices: List[Dict[str, str]] = field(default_factory=list)
    selected_serial: Optional[str] = None
    authorization_status: str = "Sin dispositivo"
    mdns_status: str = "No disponible"
    mdns_services: List[Dict[str, str]] = field(default_factory=list)
    pc_ip: str = "127.0.0.1"
    pc_all_ips: List[str] = field(default_factory=list)
    device_ip: Optional[str] = None
    ports_used: Dict[str, Any] = field(default_factory=dict)
    connectivity_test: Dict[str, Any] = field(default_factory=dict)
    android_version: str = "—"
    sdk_api: str = "—"
    cpu_abi: str = "—"
    device_model: str = "—"
    device_manufacturer: str = "—"
    storage_phone: str = "—"
    storage_pc: str = "—"
    battery_level: Optional[int] = None
    battery_status: Optional[str] = None
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adb_version": self.adb_version,
            "adb_path": self.adb_path,
            "adb_server_status": self.adb_server_status,
            "adb_server_port": self.adb_server_port,
            "adb_server_owner": self.adb_server_owner,
            "last_reconnect_issue": self.last_reconnect_issue,
            "detected_devices": self.detected_devices,
            "selected_serial": self.selected_serial,
            "authorization_status": self.authorization_status,
            "mdns_status": self.mdns_status,
            "mdns_services": self.mdns_services,
            "pc_ip": self.pc_ip,
            "pc_all_ips": self.pc_all_ips,
            "device_ip": self.device_ip,
            "ports_used": self.ports_used,
            "connectivity_test": self.connectivity_test,
            "android_version": self.android_version,
            "sdk_api": self.sdk_api,
            "cpu_abi": self.cpu_abi,
            "device_model": self.device_model,
            "device_manufacturer": self.device_manufacturer,
            "storage_phone": self.storage_phone,
            "storage_pc": self.storage_pc,
            "battery_level": self.battery_level,
            "battery_status": self.battery_status,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp,
        }


def read_devices_store(path: Path = DEVICES_JSON) -> Dict[str, Any]:
    """Lee el archivo devices.json canónico."""
    ensure_devices_store_migrated(canonical_path=path)
    if not path.is_file():
        return {"SchemaVersion": 1, "CurrentDeviceId": None, "Devices": []}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
        return {"SchemaVersion": 1, "CurrentDeviceId": None, "Devices": []}


def save_devices_store(data: Dict[str, Any], path: Path = DEVICES_JSON) -> None:
    """Guarda en devices.json de forma segura."""
    try:
        with DEVICE_STORE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            data["UpdatedUtc"] = datetime.now(timezone.utc).isoformat()
            temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Error al guardar devices.json: %s", exc)
        raise


def resolve_serial_from_config(path: Path = DEVICES_JSON) -> Optional[str]:
    """Lee el dispositivo actual del registro canónico."""
    store = read_devices_store(path)
    devices = store.get("Devices") or []
    current_id = store.get("CurrentDeviceId")
    chosen = next((d for d in devices if d.get("Id") == current_id), None)
    if chosen is None and len(devices) == 1:
        chosen = devices[0]
    if chosen:
        return chosen.get("LastEndpoint") or chosen.get("UsbSerial") or None
    return None


class DeviceService:
    """Fachada de administración visual y ADB para la GUI y PowerShell."""

    _device_locks: Dict[str, threading.RLock] = {}
    _device_locks_guard = threading.Lock()

    @classmethod
    def _lock_for_device(cls, usb_serial: Optional[str], device_id: Optional[str], endpoint: Optional[str] = None) -> threading.RLock:
        key = usb_serial or device_id or endpoint or "unknown"
        with cls._device_locks_guard:
            return cls._device_locks.setdefault(key, threading.RLock())

    @staticmethod
    @contextmanager
    def _conversion_process_lock(serial: str):
        if os.name != "nt":
            yield
            return
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        digest = hashlib.sha256(serial.encode("utf-8")).hexdigest()[:16]
        handle = kernel.CreateMutexW(None, False, f"Local\\AndroidAdbUsbWifi-{digest}")
        if not handle:
            raise OSError(ctypes.get_last_error(), "No se pudo abrir el bloqueo USB")
        acquired = False
        try:
            result = kernel.WaitForSingleObject(handle, 30000)
            acquired = result in (0, 0x80)
            if not acquired:
                raise TimeoutError("Conversión USB ocupada durante 30 segundos")
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)

    def __init__(self, serial: Optional[str] = None, devices_json_path: Path = DEVICES_JSON) -> None:
        self.devices_json = devices_json_path
        ensure_devices_store_migrated(canonical_path=self.devices_json)
        self.serial: Optional[str] = serial or resolve_serial_from_config(self.devices_json)
        self._explicitly_selected: bool = bool(serial)
        self._model_cache: Dict[str, str] = {}
        self._provisional_endpoints: Dict[str, str] = {}
        self.auto_wifi_enabled: bool = True
        self.auto_reconnect_enabled: bool = True
        self._auto_converted_serials: Set[str] = set()
        self._conversion_inflight: Set[str] = set()
        self._conversion_last_attempt: Dict[str, float] = {}
        self._usb_priority: Set[str] = set()
        self._conversion_guard = threading.Lock()
        self._supervisor_stop_callbacks: Dict[str, Callable[[], None]] = {}
        self._supervisor_restart_callbacks: Dict[str, Callable[[], None]] = {}
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_device_id: Optional[str] = None
        self._watchdog_policy = RetryPolicy()
        self._usb_monitor_stop = threading.Event()
        self._usb_monitor_thread: Optional[threading.Thread] = None
        self._device_change_listeners: Set[Callable[[], None]] = set()
        self._device_change_listeners_lock = threading.Lock()
        logger.info("Serial inicial configurado: %s", self.serial or "autodetección")

    # -------------------------------------------------------------------------
    # Supervisores (Notificaciones / Watchers / Monitores)
    # -------------------------------------------------------------------------
    def register_supervisor(
        self,
        key: str,
        on_stop: Optional[Callable[[], None]] = None,
        on_restart: Optional[Callable[[], None]] = None,
    ) -> None:
        """Registra funciones para detener o reiniciar el supervisor asociado."""
        if on_stop:
            self._supervisor_stop_callbacks[key] = on_stop
        if on_restart:
            self._supervisor_restart_callbacks[key] = on_restart

    def stop_supervisor(self, key: Optional[str] = None) -> None:
        """Detiene el supervisor del dispositivo indicado o todos los activos."""
        targets = [key] if key and key in self._supervisor_stop_callbacks else list(self._supervisor_stop_callbacks.keys())
        for k in targets:
            cb = self._supervisor_stop_callbacks.get(k)
            if cb:
                try:
                    cb()
                    logger.info("Supervisor detenido para: %s", k)
                except Exception as exc:
                    logger.warning("Error al detener supervisor para %s: %s", k, exc)

    def restart_supervisor(self, key: Optional[str] = None) -> None:
        """Reinicia el supervisor del dispositivo indicado o todos."""
        targets = [key] if key and key in self._supervisor_restart_callbacks else list(self._supervisor_restart_callbacks.keys())
        for k in targets:
            cb = self._supervisor_restart_callbacks.get(k)
            if cb:
                try:
                    cb()
                    logger.info("Supervisor reiniciado para: %s", k)
                except Exception as exc:
                    logger.warning("Error al reiniciar supervisor para %s: %s", k, exc)

    # -------------------------------------------------------------------------
    # Interfaces públicas requeridas
    # -------------------------------------------------------------------------
    def list_saved_devices(self) -> List[DeviceRecord]:
        """Devuelve la lista de dispositivos registrados en devices.json."""
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        return [DeviceRecord.from_dict(d) for d in devices]

    def get_device_details(self, device_id: str) -> Optional[DeviceRecord]:
        """Devuelve los detalles de un dispositivo por su Id."""
        for rec in self.list_saved_devices():
            if rec.id == device_id:
                return rec
        return None

    def connect_device(self, device_id: str, *, activate: bool = True, allow_cross_network: bool = False, deadline: Optional[float] = None) -> ConnectionResult:
        stored = read_devices_store(self.devices_json)
        target = next((d for d in stored.get("Devices", []) if d.get("Id") == device_id), None)
        if target:
            lock = self._lock_for_device(target.get("UsbSerial"), device_id, target.get("LastEndpoint"))
            with lock:
                return self._connect_device_locked(device_id, activate=activate, allow_cross_network=allow_cross_network, deadline=deadline)
        return ConnectionResult(False, device_id=device_id, error="Dispositivo no registrado.")

    @staticmethod
    def _current_network_bssid() -> Optional[str]:
        if os.name != "nt":
            return None
        try:
            result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True,
                                    text=True, timeout=3, check=False)
            match = re.search(r"(?im)^\s*BSSID\s*:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s*$", result.stdout)
            return match.group(1).lower() if match else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _endpoint_on_current_network(self, entry: Any) -> bool:
        known = entry.get("NetworkBssid") if isinstance(entry, dict) else None
        current = self._current_network_bssid()
        if not known or not current:
            return False
        return str(known).lower() == current

    @staticmethod
    def _endpoint_ready(client: Any, endpoint: str) -> bool:
        return any(d.serial == endpoint and d.state == "device" for d in client.list())

    @staticmethod
    def _physical_hash(identity: Optional[str]) -> Optional[str]:
        canonical = (identity or "").strip().lower()
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest() if canonical else None

    def _connected_identity_hash(self, client: Any, endpoint: str) -> Optional[str]:
        if not self._endpoint_ready(client, endpoint):
            return None
        try:
            if self._run_adb_host("-s", endpoint, "get-serialno", timeout=5).strip() != endpoint:
                return None
            return self._physical_hash(self._physical_identity(client.device(endpoint)))
        except Exception:
            return None

    def _verify_reconnect_identity(self, client: Any, endpoint: str, target: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        """Devuelve verified, provisional o rejected y la huella observada."""
        if not self._endpoint_ready(client, endpoint):
            return "OFFLINE", None
        try:
            transport = self._run_adb_host("-s", endpoint, "get-serialno", timeout=5).strip()
            if transport != endpoint:
                return "SERIAL_NO_COINCIDE", None
            observed = self._physical_hash(self._physical_identity(client.device(endpoint)))
        except Exception:
            return "SERIAL_NO_COINCIDE", None
        expected = target.get("PhysicalIdHash")
        if expected:
            return ("verified", observed) if observed == expected else ("SERIAL_NO_COINCIDE", observed)
        usb_serial = target.get("UsbSerial")
        if usb_serial and any(d.serial == usb_serial and d.state == "device" for d in client.list()):
            try:
                usb_hash = self._physical_hash(self._physical_identity(client.device(usb_serial)))
            except Exception:
                usb_hash = None
            return ("verified", observed) if usb_hash and observed == usb_hash else ("SERIAL_NO_COINCIDE", observed)
        return "provisional", observed

    @staticmethod
    def _reconnect_error(output: str) -> str:
        value = output.lower()
        if "authenticat" in value or "certificate" in value or "unauthorized" in value:
            return "AUTH_FAILED"
        if "no route" in value or "unreachable" in value or "network" in value and "unreachable" in value:
            return "NO_ROUTE_VPN"
        if "refused" in value:
            return "CONNECT_RECHAZADO"
        if "timed out" in value or "timeout" in value:
            return "TIMEOUT"
        return "CONNECT_RECHAZADO"

    def _connect_recovery_candidate(self, client: Any, endpoint: str, timeout: float,
                                    device_id: str = "unknown", stage: str = "connect") -> Tuple[bool, str]:
        # adb reconnect offline afecta a todos los transportes; limpiar solo este endpoint.
        started = time.monotonic()
        try:
            if any(d.serial == endpoint and d.state == "offline" for d in client.list()):
                try:
                    client.disconnect(endpoint)
                except Exception:
                    pass
                self._conversion_log(device_id, "offline", "OFFLINE", endpoint)
            output = str(client.connect(endpoint, timeout=timeout) or "")
            if "connected to" in output.lower() or "already connected" in output.lower():
                self._conversion_log(device_id, stage, "CONNECTED", endpoint, int((time.monotonic() - started) * 1000))
                return True, "SUCCESS"
            code = self._reconnect_error(output)
            self._conversion_log(device_id, stage, code, endpoint, int((time.monotonic() - started) * 1000))
            return False, code
        except Exception as exc:
            code = self._reconnect_error(str(exc))
            self._conversion_log(device_id, stage, code, endpoint, int((time.monotonic() - started) * 1000))
            return False, code

    def _connect_device_locked(self, device_id: str, *, activate: bool, allow_cross_network: bool, deadline: Optional[float]) -> ConnectionResult:
        """
        Reconecta un dispositivo usando la prioridad inteligente:
        1. Endpoint mDNS actual.
        2. Endpoint guardado.
        3. Historial de endpoints (hasta 10).
        4. Endpoint privado o VPN.
        5. USB como respaldo.
        Actualiza metadatos, endpoint, IP, fecha y estado en caso de éxito.
        """
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        target = next((d for d in devices if d.get("Id") == device_id), None)
        if not target:
            return ConnectionResult(
                success=False,
                device_id=device_id,
                error=f"No se encontró el dispositivo con Id '{device_id}'",
                message="Dispositivo no registrado.",
            )

        client = get_adb_client()
        last_failure = "CONNECT_RECHAZADO"

        def timeout(limit: float) -> float:
            return limit if deadline is None else max(0.5, min(limit, deadline - time.monotonic()))

        def activate_endpoint(endpoint: str) -> None:
            """Publica un cambio real de endpoint para las vistas consumidoras.

            Los puertos de Wireless Debugging cambian con frecuencia.  No basta
            con guardar el puerto nuevo: el Explorador, Backup y las colas deben
            dejar de usar el serial anterior en el mismo instante.
            """
            if not activate:
                return
            previous_serial = self.serial
            latest_store = read_devices_store(self.devices_json)
            previous_device_id = latest_store.get("CurrentDeviceId")
            self.serial = endpoint
            self._explicitly_selected = True
            latest_store["CurrentDeviceId"] = device_id
            if previous_serial != endpoint or previous_device_id != device_id:
                save_devices_store(latest_store, self.devices_json)
                self._notify_device_change()

        def finish_candidate(endpoint: str, source: str, service_name: Optional[str] = None) -> Optional[ConnectionResult]:
            nonlocal last_failure
            stage_started = time.monotonic()
            state, physical_hash = self._verify_reconnect_identity(client, endpoint, target)
            self._conversion_log(device_id, source, state.upper() if state in ("verified", "provisional") else state,
                                 endpoint, int((time.monotonic() - stage_started) * 1000))
            logger.info("Reconexión %s de %s: %s (%.0f ms)", source, endpoint, state,
                        (time.monotonic() - stage_started) * 1000)
            if state not in ("verified", "provisional"):
                last_failure = state
                return None
            if state == "provisional":
                self._provisional_endpoints[device_id] = endpoint
                activate_endpoint(endpoint)
                return ConnectionResult(True, device_id=device_id, endpoint=endpoint, method=source,
                                        provisional=True,
                                        message=f"Conexión provisional a {endpoint}; identidad pendiente de verificar por USB o vinculación manual.")
            self._provisional_endpoints.pop(device_id, None)
            self._record_successful_endpoint(target, endpoint, source=source,
                                             mdns_service_name=service_name, physical_id_hash=physical_hash)
            save_devices_store(store, self.devices_json)
            activate_endpoint(endpoint)
            self.refresh_device_metadata(device_id)
            return ConnectionResult(True, device_id=device_id, endpoint=endpoint, method=source,
                                    message=f"Conexión verificada a {endpoint} ({source}).")
        raw_list = client.list()

        # Si el USB autorizado está presente, usarlo primero. Evita bloquear
        # la detección por varios endpoints Wi-Fi antiguos o fuera de la red.
        usb_serial = target.get("UsbSerial")
        def usb_conversion_pending() -> bool:
            return bool(usb_serial and usb_serial in self._usb_priority)

        def yield_to_usb() -> ConnectionResult:
            return ConnectionResult(False, device_id=device_id, error="USB_PRIORITY",
                                    message="USB detectado; esperando conversión Wi-Fi.")

        usb_online = next((d for d in raw_list if d.serial == usb_serial and d.state == "device"), None) if usb_serial else None
        if usb_online:
            if target.get("Mode") == "tcpip-5555":
                converted, detail = self.convert_usb_to_wifi(usb_serial=usb_serial, activate=activate)
                if converted:
                    current_endpoint = read_devices_store(self.devices_json)
                    converted_record = next((d for d in current_endpoint.get("Devices", []) if d.get("Id") == device_id), None)
                    endpoint = (converted_record or {}).get("LastEndpoint")
                    if endpoint:
                        activate_endpoint(endpoint)
                        return ConnectionResult(True, device_id=device_id, endpoint=endpoint,
                                                method="usb-tcpip", message=detail)
                logger.warning("Conversión USB pendiente para %s: %s", device_id, detail)
            else:
                try:
                    usb_hash = self._physical_hash(self._physical_identity(client.device(usb_serial)))
                except Exception:
                    usb_hash = None
                if target.get("PhysicalIdHash") and usb_hash != target["PhysicalIdHash"]:
                    return ConnectionResult(False, device_id=device_id, error="SERIAL_NO_COINCIDE",
                                            message="La identidad USB no coincide con el registro.")
                if usb_hash:
                    target["PhysicalIdHash"] = usb_hash
                now_utc = datetime.now(timezone.utc).isoformat()
                target["LastConnectedUtc"] = now_utc
                target["LastSeenUtc"] = now_utc
                save_devices_store(store, self.devices_json)
                activate_endpoint(usb_serial)
                self.refresh_device_metadata(device_id)
                return ConnectionResult(True, device_id=device_id, endpoint=usb_serial,
                                        method="usb", message=f"Conectado por USB ({usb_serial}).")

        # 1. Endpoint mDNS actual
        if usb_conversion_pending():
            return yield_to_usb()
        try:
            if target.get("Mode") != "wireless-debugging-tls":
                services = []
            else:
                services = self.find_wireless_devices()
            preferred_service = (target.get("MdnsServiceName") or "").lower()
            matched_svc = None
            if preferred_service:
                matched_svc = next((s for s in services if s["service_name"].lower() == preferred_service), None)

            if matched_svc:
                ep = matched_svc["endpoint"]
                logger.info("Probando reconexión mDNS a %s para %s...", ep, target.get("FriendlyName"))
                connected, code = self._connect_recovery_candidate(client, ep, timeout(5.0), device_id, "mdns")
                if connected:
                    result = finish_candidate(ep, "mdns", matched_svc["service_name"])
                    if result:
                        return result
                else:
                    last_failure = code
        except Exception as exc:
            logger.debug("Intento mDNS no disponible: %s", exc)

        # 2. Endpoints de la red actual; el método verificado previamente ordena
        # solo estas alternativas seguras. mDNS actual siempre se intentó primero.
        saved_ep = target.get("LastEndpoint")
        saved_entry = next((entry for entry in target.get("Endpoints") or []
                            if isinstance(entry, dict) and entry.get("Endpoint") == saved_ep), None)
        local_candidates: List[Tuple[str, str]] = []
        if saved_ep and is_network_serial(saved_ep) and (allow_cross_network or self._endpoint_on_current_network(saved_entry)):
            local_candidates.append((saved_ep, "saved"))
        for ep_entry in target.get("Endpoints") or []:
            ep = ep_entry.get("Endpoint") if isinstance(ep_entry, dict) else ep_entry
            if ep and ep != saved_ep and is_network_serial(ep) and (allow_cross_network or self._endpoint_on_current_network(ep_entry)):
                local_candidates.append((ep, "history"))
        if target.get("LastSuccessfulReconnectMethod") == "history":
            local_candidates.sort(key=lambda item: 0 if item[1] == "history" else 1)
        for ep, source in local_candidates:
            if usb_conversion_pending():
                return yield_to_usb()
            if deadline is not None and time.monotonic() >= deadline:
                break
            logger.info("Probando endpoint %s de %s...", ep, source)
            connected, code = self._connect_recovery_candidate(client, ep, timeout(5.0 if source == "saved" else 3.0), device_id, source)
            if connected:
                result = finish_candidate(ep, source)
                if result:
                    return result
            else:
                last_failure = code

        # 4. Endpoint privado o VPN (NetworkProfiles)
        profiles = target.get("NetworkProfiles") or []
        for prof in profiles:
            if usb_conversion_pending():
                return yield_to_usb()
            if deadline is not None and time.monotonic() >= deadline:
                break
            pep = prof.get("PreferredEndpoint")
            if pep and is_network_serial(pep) and pep not in (saved_ep,) and allow_cross_network:
                try:
                    logger.info("Probando endpoint de perfil VPN/Red %s...", pep)
                    connected, code = self._connect_recovery_candidate(client, pep, timeout(3.0), device_id, "profile")
                    if connected:
                        result = finish_candidate(pep, "profile")
                        if result:
                            return result
                    else:
                        last_failure = code
                except Exception as exc:
                    logger.debug("Fallo al probar perfil %s: %s", pep, exc)

        # 5. USB como respaldo
        if usb_serial:
            usb_online = next((d for d in raw_list if d.serial == usb_serial and d.state == "device"), None)
            if usb_online:
                try:
                    usb_hash = self._physical_hash(self._physical_identity(client.device(usb_serial)))
                except Exception:
                    usb_hash = None
                if target.get("PhysicalIdHash") and usb_hash != target["PhysicalIdHash"]:
                    return ConnectionResult(False, device_id=device_id, error="SERIAL_NO_COINCIDE",
                                            message="La identidad USB no coincide con el registro.")
                if usb_hash:
                    target["PhysicalIdHash"] = usb_hash
                now_utc = datetime.now(timezone.utc).isoformat()
                target["LastConnectedUtc"] = now_utc
                target["LastSeenUtc"] = now_utc
                save_devices_store(store, self.devices_json)
                activate_endpoint(usb_serial)
                self.refresh_device_metadata(device_id)
                return ConnectionResult(
                    success=True,
                    device_id=device_id,
                    endpoint=usb_serial,
                    method="usb",
                    message=f"Conectado por respaldo USB ({usb_serial}).",
                )

        self._conversion_log(device_id, "reconnect", last_failure)
        return ConnectionResult(
            success=False,
            device_id=device_id,
            error=f"{last_failure}: no fue posible conectar por mDNS, endpoint guardado, historial, VPN ni USB.",
            message="Dispositivo no localizable en red ni por USB.",
        )

    def auto_reconnect_saved_devices(self) -> List[ConnectionResult]:
        """Intenta reconectar en segundo plano todos los dispositivos guardados.

        El dispositivo principal se prueba primero, seguido por los demás. Los
        fallos son no destructivos: el registro y el historial permanecen para
        que puedan intentarse de nuevo cuando cambie la red.
        """
        if not self.auto_reconnect_enabled:
            return []
        store = read_devices_store(self.devices_json)
        devices = list(store.get("Devices") or [])
        current_id = store.get("CurrentDeviceId")
        devices.sort(key=lambda item: 0 if item.get("Id") == current_id else 1)
        results: List[ConnectionResult] = []
        activated_any = False
        for device in devices:
            device_id = device.get("Id")
            if not device_id:
                continue
            try:
                # Reconectar los demás registros es útil para el inventario,
                # pero no deben robar el dispositivo que el usuario eligió.
                should_activate = device_id == current_id or (not current_id and not activated_any)
                result = self.connect_device(device_id, activate=should_activate)
            except Exception as exc:
                result = ConnectionResult(
                    success=False,
                    device_id=device_id,
                    error=str(exc),
                    message=f"No se pudo intentar la reconexión: {exc}",
                )
            results.append(result)
            if result.success:
                activated_any = activated_any or should_activate
                logger.info("Reconexión automática exitosa: %s (%s)", device.get("FriendlyName"), result.endpoint)
            else:
                logger.info("Dispositivo guardado no disponible todavía: %s", device.get("FriendlyName"))
        return results

    def reconnect_with_retry(self, device_id: str, seconds: float = 30.0, allow_cross_network: bool = False) -> ConnectionResult:
        """Reintenta fallos transitorios hasta agotar el presupuesto indicado."""
        deadline = time.monotonic() + seconds
        delay = 1.0
        result = ConnectionResult(False, device_id=device_id, error="CONNECT_RECHAZADO")
        while True:
            result = self.connect_device(device_id, deadline=deadline, allow_cross_network=allow_cross_network)
            if result.success or time.monotonic() >= deadline:
                return result
            time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
            delay = min(delay * 2, 4.0)

    def disconnect_device(self, device_id: str) -> OperationResult:
        """
        Desconecta un dispositivo:
        - Ejecuta adb disconnect para endpoints de red.
        - Detiene el supervisor del dispositivo.
        - Mantiene el registro guardado.
        - No desinstala APK auxiliares ni borra datos.
        """
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        target = next((d for d in devices if d.get("Id") == device_id), None)
        if not target:
            return OperationResult(success=False, device_id=device_id, error="Dispositivo no encontrado.")

        self.stop_supervisor(device_id)

        client = get_adb_client()
        endpoints_to_disconnect = set()
        if target.get("LastEndpoint") and is_network_serial(target["LastEndpoint"]):
            endpoints_to_disconnect.add(target["LastEndpoint"])
        for ep in target.get("Endpoints") or []:
            val = ep.get("Endpoint") if isinstance(ep, dict) else ep
            if val and is_network_serial(val):
                endpoints_to_disconnect.add(val)

        for ep in endpoints_to_disconnect:
            try:
                client.disconnect(ep)
                logger.info("Desconectado endpoint de red: %s", ep)
            except Exception as exc:
                logger.debug("Error al desconectar %s: %s", ep, exc)

        if self.serial in endpoints_to_disconnect or self.serial == target.get("UsbSerial"):
            self.serial = None
            self._explicitly_selected = False

        return OperationResult(
            success=True,
            device_id=device_id,
            message=f"Dispositivo '{target.get('FriendlyName')}' desconectado.",
        )

    def set_primary_device(self, device_id: str) -> OperationResult:
        """
        Establece como principal:
        - Guarda CurrentDeviceId.
        - Cambia la selección activa de la GUI.
        - Conecta inmediatamente mediante reconexión inteligente.
        - Reinicia el supervisor asociado al dispositivo.
        """
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        target = next((d for d in devices if d.get("Id") == device_id), None)
        if not target:
            return OperationResult(success=False, device_id=device_id, error="Dispositivo no encontrado.")

        store["CurrentDeviceId"] = device_id
        save_devices_store(store, self.devices_json)

        conn_res = self.connect_device(device_id)
        if conn_res.success:
            self.serial = conn_res.endpoint
            self._explicitly_selected = True
            self.restart_supervisor(device_id)
            return OperationResult(
                success=True,
                device_id=device_id,
                message=f"Dispositivo '{target.get('FriendlyName')}' establecido como principal y conectado.",
            )
        else:
            self.serial = target.get("LastEndpoint") or target.get("UsbSerial")
            self._explicitly_selected = True
            return OperationResult(
                success=False,
                device_id=device_id,
                message=f"Establecido como principal pero no se pudo conectar: {conn_res.error}",
                error=conn_res.error,
            )

    def rename_device(self, device_id: str, name: str) -> OperationResult:
        """
        Renombra un dispositivo guardado:
        - Valida nombre no vacío.
        - Guarda el cambio en el registro compartido.
        - Actualiza inmediatamente.
        """
        new_name = name.strip()
        if not new_name:
            return OperationResult(
                success=False,
                device_id=device_id,
                error="El nombre del dispositivo no puede estar vacío.",
            )

        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        target = next((d for d in devices if d.get("Id") == device_id), None)
        if not target:
            return OperationResult(success=False, device_id=device_id, error="Dispositivo no encontrado.")

        target["FriendlyName"] = new_name
        save_devices_store(store, self.devices_json)
        return OperationResult(
            success=True,
            device_id=device_id,
            message=f"Dispositivo renombrado a '{new_name}'.",
        )

    def remove_device(self, device_id: str) -> OperationResult:
        """
        Elimina un dispositivo:
        - Desconecta el endpoint activo.
        - Detiene el supervisor.
        - Elimina el registro de devices.json.
        - Selecciona otro dispositivo solo si existe uno único y listo.
        - No elimina APK auxiliares ni datos del teléfono.
        """
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        target = next((d for d in devices if d.get("Id") == device_id), None)
        if not target:
            return OperationResult(success=False, device_id=device_id, error="Dispositivo no encontrado.")

        ep = target.get("LastEndpoint")
        if ep and is_network_serial(ep):
            try:
                get_adb_client().disconnect(ep)
            except Exception:
                pass

        self.stop_supervisor(device_id)

        store["Devices"] = [d for d in devices if d.get("Id") != device_id]

        if store.get("CurrentDeviceId") == device_id:
            store["CurrentDeviceId"] = None
            if self.serial in (target.get("LastEndpoint"), target.get("UsbSerial")):
                self.serial = None
                self._explicitly_selected = False

        remaining = store.get("Devices") or []
        if len(remaining) == 1:
            store["CurrentDeviceId"] = remaining[0].get("Id")
            self.serial = remaining[0].get("LastEndpoint") or remaining[0].get("UsbSerial")
            self._explicitly_selected = True

        save_devices_store(store, self.devices_json)
        return OperationResult(
            success=True,
            device_id=device_id,
            message=f"Dispositivo '{target.get('FriendlyName')}' eliminado del registro.",
        )

    def refresh_device_metadata(self, device_id: str) -> Optional[DeviceRecord]:
        """
        Actualiza las propiedades de un dispositivo desde ADB:
        - Si está activo, consulta modelo, versión Android y batería.
        - Si falla, conserva los últimos valores conocidos y marca el estado.
        """
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        target = next((d for d in devices if d.get("Id") == device_id), None)
        if not target:
            return None

        client = get_adb_client()
        serial_to_query = target.get("LastEndpoint") or target.get("UsbSerial")
        now_utc = datetime.now(timezone.utc).isoformat()

        if serial_to_query:
            try:
                online_info = next((item for item in client.list() if item.serial == serial_to_query), None)
                if online_info and online_info.state == "device":
                    dev = client.device(serial_to_query)
                    target["Model"] = dev.getprop("ro.product.model") or target.get("Model")
                    target["AndroidVersion"] = dev.getprop("ro.build.version.release") or target.get("AndroidVersion")
                    bat = self._get_battery_level(dev)
                    if bat is not None:
                        target["Battery"] = bat
                    target["LastSeenUtc"] = now_utc
                    save_devices_store(store, self.devices_json)
            except Exception as exc:
                logger.debug("No se pudo refrescar metadatos de %s: %s", serial_to_query, exc)

        return DeviceRecord.from_dict(target)

    # -------------------------------------------------------------------------
    # Deduplicación y Selección Segura en get_status()
    # -------------------------------------------------------------------------
    def get_status(self) -> DeviceStatus:
        """
        Consulta no invasiva del estado ADB y de los dispositivos disponibles.
        Aplica deduplicación por prioridad y selección segura (sin selección silenciosa
        cuando hay múltiples dispositivos listos).
        """
        status = DeviceStatus(serial=self.serial)
        store = read_devices_store(self.devices_json)
        saved_records = store.get("Devices") or []
        current_id = store.get("CurrentDeviceId")
        status.primary_device_id = current_id

        try:
            client = get_adb_client()
            raw_list = client.list()
        except Exception as exc:
            status.adb_state = "daemon ADB no disponible"
            status.error = f"Error al comunicar con ADB: {exc}"
            return status

        mdns_map: Dict[str, str] = {}
        try:
            for s in self.find_wireless_devices():
                mdns_map[s["endpoint"]] = s["service_name"]
        except Exception:
            pass

        available_infos: List[DeviceInfo] = []
        for info in raw_list:
            is_net = is_network_serial(info.serial)
            mdns_svc = mdns_map.get(info.serial)
            matched_saved = self._match_online_to_saved(info.serial, mdns_svc, saved_records)

            dev_id = matched_saved.get("Id") if matched_saved else None
            friendly = matched_saved.get("FriendlyName") if matched_saved else None
            model = matched_saved.get("Model") if matched_saved else self._model_cache.get(info.serial)
            android_ver = matched_saved.get("AndroidVersion") if matched_saved else None
            battery = matched_saved.get("Battery") if matched_saved else None
            last_ep = matched_saved.get("LastEndpoint") if matched_saved else None
            mode = matched_saved.get("Mode") if matched_saved else ("tcpip-5555" if is_net else "usb")
            last_conn = matched_saved.get("LastConnectedUtc") if matched_saved else None
            last_seen = matched_saved.get("LastSeenUtc") if matched_saved else None
            is_prim = bool(dev_id and dev_id == current_id)

            if info.state == "device":
                try:
                    dev = client.device(info.serial)
                    if not model:
                        model = dev.getprop("ro.product.model") or ("Wi-Fi Phone" if is_net else "USB Phone")
                        self._model_cache[info.serial] = model
                    if not android_ver:
                        android_ver = dev.getprop("ro.build.version.release")
                    bat = self._get_battery_level(dev)
                    if bat is not None:
                        battery = bat
                except Exception:
                    pass

            available_infos.append(
                DeviceInfo(
                    serial=info.serial,
                    state=info.state,
                    is_network=is_net,
                    device_id=dev_id,
                    friendly_name=friendly or model or info.serial,
                    model=model or "Android",
                    battery=battery,
                    android_version=android_ver,
                    mode=mode,
                    last_endpoint=last_ep or info.serial,
                    last_connected_utc=last_conn,
                    last_seen_utc=last_seen,
                    is_primary=is_prim,
                )
            )

        status.available_devices = available_infos

        if not available_infos:
            status.adb_state = "desconectado"
            status.error = "No hay ningún teléfono detectado por USB ni por Wi-Fi."
            return status

        current_connected_serials = {d.serial for d in available_infos}
        self._auto_converted_serials.intersection_update(current_connected_serials)

        devices_in_device_state = [d for d in available_infos if d.state == "device"]

        physical_groups: Dict[str, List[DeviceInfo]] = {}
        for d in devices_in_device_state:
            key = d.device_id or d.serial
            physical_groups.setdefault(key, []).append(d)

        num_ready_physical_devices = len(physical_groups)

        if num_ready_physical_devices == 0:
            target_info = available_infos[0]
            status.connected = False
            status.adb_state = target_info.state
            status.serial = target_info.serial
            if target_info.state == "unauthorized":
                status.error = (
                    f"El dispositivo {target_info.serial} requiere AUTORIZACIÓN.\n"
                    "Desbloquea la pantalla de tu celular y pulsa 'Permitir siempre la depuración USB'."
                )
            else:
                status.error = f"Dispositivo {target_info.serial} en estado '{target_info.state}'."
            return status

        elif num_ready_physical_devices == 1:
            group = list(physical_groups.values())[0]
            chosen_info = next((d for d in group if d.is_network), group[0])
            self.serial = chosen_info.serial
            status.requires_selection = False
            target_info = chosen_info

        else:
            matching_active = next((d for d in devices_in_device_state if d.serial == self.serial), None)
            if matching_active and self._explicitly_selected:
                target_info = matching_active
                status.requires_selection = False
            else:
                status.requires_selection = True
                status.connected = False
                status.adb_state = "múltiples dispositivos"
                status.error = (
                    f"Hay {num_ready_physical_devices} dispositivos Android conectados listos.\n"
                    "Selecciona obligatoriamente cuál deseas utilizar en el panel lateral."
                )
                return status

        status.serial = self.serial
        status.adb_state = target_info.state
        status.connected = True
        status.model = target_info.model or "Android"
        status.battery = target_info.battery
        status.android_version = target_info.android_version

        try:
            dev = client.device(self.serial)
            battery_dump = dev.shell("dumpsys battery") or ""
            match_temp = re.search(r"temperature:\s*(\d+)", battery_dump)
            if match_temp:
                status.temperature = round(float(match_temp.group(1)) / 10.0, 1)
        except Exception as exc:
            logger.debug("Error leyendo temperatura de %s: %s", self.serial, exc)
            status.is_stale = True

        return status

    def _match_online_to_saved(
        self,
        serial: str,
        mdns_service: Optional[str],
        saved_records: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Aplica la prioridad de identidad para asociar un endpoint detectado con un registro guardado."""
        s_norm = serial.strip().lower()

        # 1. Mismo UsbSerial
        for rec in saved_records:
            usb = (rec.get("UsbSerial") or "").strip().lower()
            if usb and usb == s_norm:
                return rec

        # 2. Mismo MdnsServiceName
        if mdns_service:
            svc_norm = mdns_service.strip().lower()
            for rec in saved_records:
                saved_svc = (rec.get("MdnsServiceName") or "").strip().lower()
                if saved_svc and saved_svc == svc_norm:
                    return rec

        # 3. Mismo endpoint / LastEndpoint / Endpoints
        for rec in saved_records:
            last_ep = (rec.get("LastEndpoint") or "").strip().lower()
            if last_ep and last_ep == s_norm:
                return rec
            for ep_entry in rec.get("Endpoints") or []:
                ep_val = (ep_entry.get("Endpoint") if isinstance(ep_entry, dict) else ep_entry) or ""
                if ep_val.strip().lower() == s_norm:
                    return rec

        return None

    def _record_successful_endpoint(
        self,
        record: Dict[str, Any],
        endpoint: str,
        source: str = "tcpip",
        mdns_service_name: Optional[str] = None,
        physical_id_hash: Optional[str] = None,
    ) -> None:
        """Agrega un endpoint al historial (conservando hasta 10) y actualiza metadatos."""
        now_utc = datetime.now(timezone.utc).isoformat()
        record["LastEndpoint"] = endpoint
        if physical_id_hash:
            record["PhysicalIdHash"] = physical_id_hash
        record["LastSuccessfulReconnectMethod"] = source
        record["LastConnectedUtc"] = now_utc
        record["LastSeenUtc"] = now_utc
        if ":" in endpoint:
            record["LastIp"] = endpoint.rsplit(":", 1)[0].strip("[]")
        if mdns_service_name:
            record["MdnsServiceName"] = mdns_service_name

        endpoints_list = record.setdefault("Endpoints", [])
        endpoints_list[:] = [e for e in endpoints_list if (isinstance(e, dict) and e.get("Endpoint") != endpoint)]
        endpoints_list.insert(
            0,
            {
                "Endpoint": endpoint,
                "ConnectedUtc": now_utc,
                "MdnsServiceName": mdns_service_name or record.get("MdnsServiceName"),
                "Source": source,
                "Status": "device",
                "NetworkBssid": self._current_network_bssid(),
            },
        )
        del endpoints_list[10:]
        self._conversion_log(str(record.get("Id") or "unknown"), source, "SUCCESS", endpoint)

    # -------------------------------------------------------------------------
    # Datos consolidados para la administración visual (Tarjetas)
    # -------------------------------------------------------------------------
    def get_management_data(self) -> Dict[str, Any]:
        """
        Genera la lista consolidada de tarjetas de dispositivos guardados y detectados,
        con agrupación de duplicados, estado actual y acciones.
        """
        store = read_devices_store(self.devices_json)
        saved_list = list(store.get("Devices") or [])
        current_id = store.get("CurrentDeviceId")

        online_map: Dict[str, Any] = {}
        try:
            client = get_adb_client()
            for info in client.list():
                online_map[info.serial] = info
        except Exception as exc:
            logger.debug("Error listando ADB para tarjetas: %s", exc)

        # Registrar automáticamente USB autorizados que nunca habían sido
        # guardados. Un dispositivo ``unauthorized`` no se persiste hasta que
        # el usuario acepta la clave RSA en el teléfono.
        changed_store = False
        for serial, info in online_map.items():
            if is_network_serial(serial) or info.state != "device":
                continue
            known = next(
                (item for item in saved_list if item.get("UsbSerial") == serial or item.get("LastEndpoint") == serial),
                None,
            )
            if known:
                continue
            new_id = uuid.uuid4().hex
            saved_list.append({
                "Id": new_id,
                "FriendlyName": f"USB Android ({serial})",
                "UsbSerial": serial,
                "LastEndpoint": serial,
                "Mode": "usb",
                "IsPaired": True,
                "NetworkProfiles": [],
                "Endpoints": [],
                "LastSeenUtc": datetime.now(timezone.utc).isoformat(),
            })
            if not current_id and len(saved_list) == 1:
                current_id = new_id
                store["CurrentDeviceId"] = new_id
                self.serial = serial
                self._explicitly_selected = True
            changed_store = True
            logger.info("USB autorizado registrado automáticamente: %s", serial)
        if changed_store:
            save_devices_store(store, self.devices_json)

        cards: List[Dict[str, Any]] = []
        matched_online_serials: Set[str] = set()

        for s in saved_list:
            device_id = s.get("Id")
            friendly = s.get("FriendlyName") or "Android"
            model = s.get("Model") or "—"
            usb = s.get("UsbSerial")
            last_ep = s.get("LastEndpoint")
            battery = s.get("Battery")
            android_ver = s.get("AndroidVersion") or "—"
            mode = s.get("Mode") or "tcpip-5555"
            last_conn = s.get("LastConnectedUtc") or "—"

            provisional_ep = self._provisional_endpoints.get(device_id)
            provisional_online = online_map.get(provisional_ep) if provisional_ep else None
            ep_online = provisional_online or (online_map.get(last_ep) if last_ep else None)
            usb_online = online_map.get(usb) if usb else None
            if ep_online and ep_online.state == "device" and not s.get("PhysicalIdHash") and not usb_online:
                self._provisional_endpoints.setdefault(device_id, ep_online.serial)
                provisional_ep = ep_online.serial
                provisional_online = ep_online

            active_info = ep_online or usb_online
            if active_info:
                if ep_online:
                    matched_online_serials.add(ep_online.serial)
                if usb_online:
                    matched_online_serials.add(usb)

            if active_info:
                if active_info.state == "device":
                    current_state = "conectado"
                elif active_info.state == "unauthorized":
                    current_state = "no autorizado"
                else:
                    current_state = "offline"
                current_endpoint = active_info.serial
            else:
                current_state = "no detectado"
                current_endpoint = last_ep or usb or "—"

            associated_endpoints = []
            if last_ep:
                associated_endpoints.append(f"Wi-Fi: {last_ep}")
            if usb:
                associated_endpoints.append(f"USB: {usb}")

            is_prim = (device_id == current_id)
            is_active = (self.serial in (last_ep, usb, provisional_ep) and bool(self.serial))

            cards.append({
                "id": device_id,
                "friendly_name": friendly,
                "model": model,
                "battery": battery,
                "android_version": android_ver,
                "mode": mode,
                "current_endpoint": current_endpoint,
                "last_connected": last_conn,
                "state": current_state,
                "is_primary": is_prim,
                "is_active": is_active,
                "is_saved": True,
                "usb_serial": usb,
                "associated_endpoints": associated_endpoints,
                "endpoints_history": s.get("Endpoints") or [],
                "provisional": bool(provisional_online and provisional_online.state == "device"),
            })

        for serial, info in online_map.items():
            if serial not in matched_online_serials:
                is_net = is_network_serial(serial)
                cards.append({
                    "id": None,
                    "friendly_name": f"Detectado: {serial}",
                    "model": "Desconocido",
                    "battery": None,
                    "android_version": "—",
                    "mode": "tcpip-5555" if is_net else "usb",
                    "current_endpoint": serial,
                    "last_connected": "En línea ahora",
                    "state": "conectado" if info.state == "device" else info.state,
                    "is_primary": False,
                    "is_active": (self.serial == serial),
                    "is_saved": False,
                    "usb_serial": serial if not is_net else None,
                    "associated_endpoints": [serial],
                    "endpoints_history": [],
                })

        return {
            "cards": cards,
            "current_device_id": current_id,
            "active_serial": self.serial,
        }

    def set_active_by_serial(self, serial: str) -> Tuple[bool, str]:
        """Establece un serial como dispositivo activo y actualiza devices.json."""
        self.serial = serial
        self._explicitly_selected = True
        store = read_devices_store(self.devices_json)
        devices = store.get("Devices") or []
        match = next((d for d in devices if d.get("LastEndpoint") == serial or d.get("UsbSerial") == serial), None)
        if match:
            store["CurrentDeviceId"] = match.get("Id")
            save_devices_store(store, self.devices_json)
            return True, f"Dispositivo activo cambiado a '{match.get('FriendlyName')}' ({serial})."
        return True, f"Dispositivo activo cambiado a: {serial}."

    def set_active_by_saved_id(self, device_id: str, retry_seconds: float = 30.0, allow_cross_network: bool = False) -> Tuple[bool, str]:
        """Activa un dispositivo guardado reconectándolo si es necesario."""
        res = self.reconnect_with_retry(device_id, seconds=retry_seconds, allow_cross_network=allow_cross_network)
        if res.success:
            self.serial = res.endpoint
            self._explicitly_selected = True
            store = read_devices_store(self.devices_json)
            store["CurrentDeviceId"] = device_id
            save_devices_store(store, self.devices_json)
            return True, res.message
        return False, res.error or res.message

    def confirm_provisional_identity(self, device_id: str) -> OperationResult:
        """Vincula explícitamente el endpoint provisional visible en esta sesión."""
        endpoint = self._provisional_endpoints.get(device_id)
        if not endpoint:
            return OperationResult(False, device_id=device_id, error="No hay conexión provisional activa.")
        store = read_devices_store(self.devices_json)
        record = next((d for d in store.get("Devices", []) if d.get("Id") == device_id), None)
        if not record:
            return OperationResult(False, device_id=device_id, error="Dispositivo no registrado.")
        client = get_adb_client()
        state, observed_hash = self._verify_reconnect_identity(client, endpoint, record)
        if state not in ("verified", "provisional") or not observed_hash:
            return OperationResult(False, device_id=device_id, error=f"No se pudo verificar el endpoint: {state}.")
        self._record_successful_endpoint(record, endpoint, source="manual-bind", physical_id_hash=observed_hash)
        save_devices_store(store, self.devices_json)
        self._provisional_endpoints.pop(device_id, None)
        return OperationResult(True, device_id=device_id, message=f"Identidad de {endpoint} vinculada y guardada.")

    # -------------------------------------------------------------------------
    # Auto-conversión USB a Wi-Fi y utilidades
    # -------------------------------------------------------------------------
    def init_adb(self) -> None:
        """Arranca o verifica el daemon ADB."""
        ensure_single_adb_server(force=False)

    def add_device_change_listener(self, callback: Callable[[], None]) -> None:
        """Registra un callback para cambios asíncronos de conexión."""
        with self._device_change_listeners_lock:
            self._device_change_listeners.add(callback)

    def remove_device_change_listener(self, callback: Callable[[], None]) -> None:
        """Elimina un callback registrado previamente."""
        with self._device_change_listeners_lock:
            self._device_change_listeners.discard(callback)

    def _notify_device_change(self) -> None:
        """Notifica cambios sin permitir que un consumidor detenga ADB."""
        with self._device_change_listeners_lock:
            callbacks = tuple(self._device_change_listeners)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.debug("Un observador de dispositivos no pudo actualizarse.", exc_info=True)

    def start_usb_auto_connect_monitor(self) -> None:
        """Vigila ADB en segundo plano y convierte USB autorizado a Wi-Fi."""
        if self._usb_monitor_thread and self._usb_monitor_thread.is_alive():
            return
        self._usb_monitor_stop.clear()

        def monitor() -> None:
            while not self._usb_monitor_stop.is_set():
                try:
                    self.check_and_auto_convert_usb()
                except Exception as exc:
                    logger.debug("Error en monitor USB automático: %s", exc)
                self._usb_monitor_stop.wait(1.0)

        self._usb_monitor_thread = threading.Thread(target=monitor, name="usb-auto-connect", daemon=True)
        self._usb_monitor_thread.start()
        logger.info("Monitor automático USB→Wi-Fi iniciado.")

    def stop_usb_auto_connect_monitor(self) -> None:
        """Detiene el monitor USB al cerrar la aplicación."""
        self._usb_monitor_stop.set()

    def check_and_auto_convert_usb(self, on_result: Optional[Callable[[bool, str], None]] = None) -> None:
        """Convierte cada USB legado listo sin bloquear la deteccion de otros telefonos."""
        if not self.auto_wifi_enabled:
            return
        try:
            available = get_adb_client().list()
            store = read_devices_store(self.devices_json)
            saved = {
                d.get("UsbSerial"): d for d in store.get("Devices", [])
                if d.get("UsbSerial") and d.get("Mode") == "tcpip-5555"
            }
            current_usb = {d.serial for d in available if not is_network_serial(d.serial)}
            self._auto_converted_serials.intersection_update(current_usb)
            for device in available:
                serial = device.serial
                if (device.state != "device" or serial not in saved or serial in self._auto_converted_serials
                        or time.monotonic() - self._conversion_last_attempt.get(serial, 0.0) < 30.0):
                    continue
                with self._conversion_guard:
                    if serial in self._conversion_inflight:
                        continue
                    self._conversion_inflight.add(serial)
                    self._usb_priority.add(serial)
                    self._conversion_last_attempt[serial] = time.monotonic()

                def worker(usb_serial: str = serial) -> None:
                    try:
                        ok, message = self.convert_usb_to_wifi(usb_serial=usb_serial, activate=False)
                        if ok:
                            self._auto_converted_serials.add(usb_serial)
                        if on_result:
                            on_result(ok, message)
                    except Exception as exc:
                        logger.warning("Auto-conversion USB fallida para %s: %s", usb_serial, exc)
                        self._conversion_log(str(saved[usb_serial].get("Id")), "conversion", "TCPIP_FALLO")
                        if on_result:
                            on_result(False, f"TCPIP_FALLO: {exc}")
                    finally:
                        with self._conversion_guard:
                            self._conversion_inflight.discard(usb_serial)
                            self._usb_priority.discard(usb_serial)

                threading.Thread(target=worker, name=f"auto-wifi-{serial}", daemon=True).start()
        except Exception as exc:
            logger.warning("No se pudo revisar USB para auto-conversion: %s", exc)

    def _conversion_log(self, device_id: str, stage: str, code: str, endpoint: Optional[str] = None,
                        duration_ms: Optional[int] = None) -> None:
        """Guarda un resumen pequeno y rotativo de los intentos."""
        path = self.devices_json.parent / "reconnect-attempts.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 1024 * 1024:
                old = path.with_suffix(".jsonl.1")
                old.unlink(missing_ok=True)
                path.replace(old)
            entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "device_id": device_id,
                     "stage": stage, "code": code, "endpoint": endpoint, "duration_ms": duration_ms}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("No se pudo guardar el diagnostico de reconexion", exc_info=True)

    @staticmethod
    def _physical_identity(device: Any) -> Optional[str]:
        for key in ("ro.serialno", "ro.boot.serialno"):
            try:
                value = str(device.getprop(key) or "").strip()
            except Exception:
                continue
            if value and value.lower() not in ("unknown", "null", "none"):
                return value
        try:
            android_id = str(device.shell("settings get secure android_id") or "").strip()
            if re.fullmatch(r"[0-9a-fA-F]{16}", android_id):
                return f"android-id:{android_id.lower()}"
        except Exception:
            pass
        return None

    @staticmethod
    def _usb_wifi_ip(device: Any) -> Optional[str]:
        for command in ("ip route", "ip addr", "ip -4 addr show wlan0"):
            try:
                output = str(device.shell(command) or "")
            except Exception:
                continue
            if command == "ip route":
                output = "\n".join(line for line in output.splitlines() if "wlan" in line)
            elif command == "ip addr":
                blocks = re.split(r"(?m)^\d+: ", output)
                output = "\n".join(block for block in blocks if block.startswith("wlan"))
            matches = re.findall(r"(?:\bsrc\s+|\binet\s+)(\d{1,3}(?:\.\d{1,3}){3})", output)
            for candidate in matches:
                try:
                    ip = ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if isinstance(ip, ipaddress.IPv4Address) and not ip.is_loopback and not ip.is_link_local:
                    return candidate
        try:
            candidate = str(device.wlan_ip() or "")
            address = ipaddress.ip_address(candidate)
            if isinstance(address, ipaddress.IPv4Address) and not address.is_loopback and not address.is_link_local:
                return candidate
        except (ValueError, AttributeError, OSError):
            pass
        return None

    def convert_usb_to_wifi(self, usb_serial: Optional[str] = None, *, activate: bool = True) -> Tuple[bool, str]:
        """Convierte un USB legado y conserva su identidad fisica."""
        serial = usb_serial or self.serial
        if not serial or is_network_serial(serial):
            return False, "USB_NO_DETECTADO: selecciona un dispositivo USB."
        store = read_devices_store(self.devices_json)
        target = next((d for d in store.get("Devices", []) if d.get("UsbSerial") == serial), None)
        if not target or target.get("Mode") != "tcpip-5555":
            return False, "USB_NO_DETECTADO: solo se convierten dispositivos TCP/IP legado registrados."
        device_id = str(target["Id"])
        endpoint_old = target.get("LastEndpoint")
        lock = self._lock_for_device(serial, device_id, endpoint_old)
        with lock, self._conversion_process_lock(serial):
            client = get_adb_client()
            raw = next((d for d in client.list() if d.serial == serial), None)
            if not raw:
                self._conversion_log(device_id, "precondition", "USB_NO_DETECTADO")
                return False, "USB_NO_DETECTADO: el telefono ya no esta conectado."
            if raw.state != "device":
                code = "USB_UNAUTHORIZED" if raw.state == "unauthorized" else "OFFLINE"
                self._conversion_log(device_id, "precondition", code)
                return False, f"{code}: autoriza o reactiva el telefono."
            dev = client.device(serial)
            physical_id = self._physical_identity(dev)
            physical_hash = self._physical_hash(physical_id)
            try:
                model = dev.getprop("ro.product.model") or "Android"
            except Exception:
                model = "Android"
            if not physical_id:
                self._conversion_log(device_id, "identity", "SERIAL_NO_COINCIDE")
                return False, "SERIAL_NO_COINCIDE: no se pudo leer la identidad fisica por USB."
            if target.get("PhysicalIdHash") and target["PhysicalIdHash"] != physical_hash:
                return False, "SERIAL_NO_COINCIDE: la identidad USB no coincide con el registro."
            for online in client.list():
                if online.state != "device" or not is_network_serial(online.serial):
                    continue
                try:
                    wifi_dev = client.device(online.serial)
                    if self._physical_identity(wifi_dev) == physical_id:
                        transport = self._run_adb_host("-s", online.serial, "get-serialno", timeout=5).strip()
                        if transport != online.serial:
                            continue
                        self._save_wifi_device_to_store(model=model, usb_serial=serial,
                                                        endpoint=online.serial, activate=activate,
                                                        physical_id_hash=physical_hash)
                        if activate or store.get("CurrentDeviceId") == device_id:
                            self.serial = online.serial
                            self._explicitly_selected = True
                        self._conversion_log(device_id, "existing", "SUCCESS", online.serial)
                        return True, f"Wi-Fi ya activo en {online.serial}."
                except Exception:
                    pass
            ip = self._usb_wifi_ip(dev)
            if not ip:
                self._conversion_log(device_id, "ip", "IP_NO_ENCONTRADA")
                return False, "IP_NO_ENCONTRADA: revisa el Wi-Fi del telefono."
            endpoint = f"{ip}:5555"
            self._conversion_log(device_id, "tcpip", "START", endpoint)
            tcpip_failed = False
            try:
                dev.tcpip(5555)
            except Exception as exc:
                tcpip_failed = True
                logger.warning("ADB reinicio o perdio USB durante tcpip: %s", exc)
            deadline = time.monotonic() + 15
            last_error = "TCPIP_FALLO" if tcpip_failed else "ADBD_NO_VOLVIO"
            while time.monotonic() < deadline:
                try:
                    response = str(client.connect(endpoint, timeout=4.0) or "")
                    if not any(word in response.lower() for word in ("connected to", "already connected")):
                        last_error = "CONNECT_RECHAZADO"
                    else:
                        network = next((d for d in client.list() if d.serial == endpoint), None)
                        if network and network.state == "device":
                            transport_serial = self._run_adb_host("-s", endpoint, "get-serialno", timeout=5).strip()
                            if transport_serial != endpoint:
                                last_error = "SERIAL_NO_COINCIDE"
                                break
                            wifi_dev = client.device(endpoint)
                            if self._physical_identity(wifi_dev) != physical_id:
                                last_error = "SERIAL_NO_COINCIDE"
                                break
                            self._save_wifi_device_to_store(model=model,
                                                            usb_serial=serial, endpoint=endpoint,
                                                            activate=activate, physical_id_hash=physical_hash)
                            if activate or store.get("CurrentDeviceId") == device_id:
                                self.serial = endpoint
                                self._explicitly_selected = True
                            self._auto_converted_serials.add(serial)
                            self._conversion_log(device_id, "verified", "SUCCESS", endpoint)
                            self._notify_device_change()
                            return True, f"Conexion Wi-Fi verificada en {endpoint}."
                        last_error = "OFFLINE"
                except Exception as exc:
                    logger.debug("Conexion Wi-Fi pendiente: %s", exc)
                time.sleep(1)
            self._conversion_log(device_id, "verify", last_error, endpoint)
            return False, f"{last_error}: no se verifico la conexion Wi-Fi de {serial}."

    def _save_wifi_device_to_store(self, model: str, usb_serial: str, endpoint: str, activate: bool = True,
                                   physical_id_hash: Optional[str] = None) -> None:
        """Persiste dispositivo Wi-Fi en devices.json."""
        with DEVICE_STORE_LOCK:
            store = read_devices_store(self.devices_json)
            devices = store.setdefault("Devices", [])
            existing = next((d for d in devices if d.get("UsbSerial") == usb_serial), None)
            if existing:
                self._record_successful_endpoint(existing, endpoint, source="tcpip", physical_id_hash=physical_id_hash)
                existing["Mode"] = "tcpip-5555"
                device_id = existing["Id"]
            else:
                device_id = uuid.uuid4().hex
                new_dev = {
                    "Id": device_id,
                    "FriendlyName": f"{model} ({endpoint})",
                    "UsbSerial": usb_serial,
                    "LastEndpoint": endpoint,
                    "Mode": "tcpip-5555",
                    "LastConnectedUtc": datetime.now(timezone.utc).isoformat(),
                    "IsPaired": True,
                    "NetworkProfiles": [
                        {
                            "Id": uuid.uuid4().hex,
                            "Name": "Wi-Fi Principal",
                            "PreferredEndpoint": endpoint,
                            "ForceUsbOnly": False,
                        }
                    ],
                }
                self._record_successful_endpoint(new_dev, endpoint, source="tcpip", physical_id_hash=physical_id_hash)
                devices.append(new_dev)

            if activate or not store.get("CurrentDeviceId"):
                store["CurrentDeviceId"] = device_id
            save_devices_store(store, self.devices_json)

    @staticmethod
    def _run_adb_host(*arguments: str, timeout: float = 15.0) -> str:
        """Ejecuta un comando ADB del host (pair, mdns o connect)."""
        command = [get_adb_executable(), *arguments]
        kwargs: Dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": False,
            "env": {**os.environ, "ADB_MDNS_OPENSCREEN": "1"},
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ADB no respondió a tiempo: {' '.join(arguments)}") from exc
        output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        if result.returncode != 0:
            raise RuntimeError(output or f"ADB falló: {' '.join(arguments)}")
        return output

    def find_wireless_devices(self) -> List[Dict[str, str]]:
        """Descubre endpoints Wireless Debugging publicados por mDNS."""
        output = self._run_adb_host("mdns", "services")
        services: List[Dict[str, str]] = []
        for line in output.splitlines():
            if "adb-tls-connect" not in line:
                continue
            match = re.search(r"(?P<host>\[[^\]]+\]|[A-Za-z0-9._-]+):(?P<port>\d{1,5})", line)
            if not match:
                continue
            host = match.group("host").strip("[]")
            endpoint = f"[{host}]:{match.group('port')}" if ":" in host else f"{host}:{match.group('port')}"
            if any(item.get("endpoint") == endpoint for item in services):
                continue
            services.append({
                "service_name": line.strip().split()[0] if line.strip() else "",
                "host": host,
                "port": match.group("port"),
                "endpoint": endpoint,
                "raw": line.strip(),
            })
        return services

    def pair_wireless(
        self,
        pair_host: str,
        pair_port: int,
        pairing_code: str,
        friendly_name: str = "",
        usb_serial: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Empareja el teléfono por Wireless Debugging y conecta su servicio mDNS."""
        host = pair_host.strip().strip("[]")
        try:
            numeric_port = int(pair_port)
        except (TypeError, ValueError):
            numeric_port = 0
        if not host or not (1 <= numeric_port <= 65535):
            return False, "El host o puerto de emparejamiento no es válido."
        if not re.fullmatch(r"\d{6}", pairing_code.strip()):
            return False, "El código de emparejamiento debe tener 6 dígitos."
        try:
            endpoint = f"[{host}]:{numeric_port}" if ":" in host else f"{host}:{numeric_port}"
            output = self._run_adb_host("pair", endpoint, pairing_code.strip(), timeout=20.0)
            if re.search(r"failed|error|cannot", output, re.IGNORECASE):
                return False, f"El emparejamiento falló: {output.strip()}"
            deadline = time.monotonic() + 15.0
            services: List[Dict[str, str]] = []
            while time.monotonic() < deadline and not services:
                try:
                    services = self.find_wireless_devices()
                except Exception:
                    services = []
                if not services:
                    time.sleep(1.0)
            if not services:
                store = read_devices_store(self.devices_json)
                devices = store.setdefault("Devices", [])
                matching_dev = next((d for d in devices if any(p.get("Host") == host for p in d.get("NetworkProfiles", []))), None)
                if not matching_dev:
                    dev_id = uuid.uuid4().hex
                    matching_dev = {
                        "Id": dev_id,
                        "FriendlyName": friendly_name.strip() or f"Android Wireless ({host})",
                        "UsbSerial": usb_serial if usb_serial and not is_network_serial(usb_serial) else None,
                        "MdnsServiceName": "",
                        "Mode": "wireless-debugging-tls",
                        "IsPaired": True,
                        "NetworkProfiles": [],
                    }
                    devices.append(matching_dev)
                else:
                    matching_dev["IsPaired"] = True
                save_devices_store(store, self.devices_json)
                return True, (
                    f"¡Emparejamiento exitoso con {host}! Tu red bloquea el anuncio mDNS. "
                    f"Para conectar: ve a 'Conexión Directa', pon la IP y puerto principal que muestra tu celular (ej. {host}:PUERTO) y pulsa 'Conectar Endpoint'."
                )
            matching = [item for item in services if item["host"].lower() == host.lower()]
            selected = matching[0] if len(matching) == 1 else services[0] if len(services) == 1 else None
            if selected is None:
                return False, "Hay varios teléfonos mDNS; usa Buscar dispositivo WiFi para seleccionar uno."

            client = get_adb_client()
            res = client.connect(selected["endpoint"], timeout=12.0).strip()
            physical_hash = self._connected_identity_hash(client, selected["endpoint"])
            if not physical_hash:
                return False, f"SERIAL_NO_COINCIDE: no se verificó el endpoint {selected['endpoint']} ({res})."
            store = read_devices_store(self.devices_json)
            devices = store.setdefault("Devices", [])
            dev_id = uuid.uuid4().hex
            record = {
                "Id": dev_id,
                "FriendlyName": friendly_name.strip() or f"Android Wireless ({selected['host']})",
                "UsbSerial": usb_serial if usb_serial and not is_network_serial(usb_serial) else None,
                "MdnsServiceName": selected["service_name"],
                "Mode": "wireless-debugging-tls",
                "IsPaired": True,
                "NetworkProfiles": [],
            }
            self._record_successful_endpoint(record, selected["endpoint"], source="mdns", mdns_service_name=selected["service_name"], physical_id_hash=physical_hash)
            devices.append(record)
            store["CurrentDeviceId"] = dev_id
            save_devices_store(store, self.devices_json)

            self.serial = selected["endpoint"]
            self._explicitly_selected = True
            return True, f"Conectado por Wireless Debugging a {selected['endpoint']} ({res})."
        except Exception as exc:
            logger.warning("No se pudo configurar Wireless Debugging: %s", exc)
            return False, f"No se pudo configurar Wireless Debugging: {exc}"

    def pair_wireless_qr(
        self,
        friendly_name: str = "",
        timeout: float = 120.0,
        progress: Optional[Callable[[str], None]] = None,
        qr_service=None,
        qr_payload=None,
    ) -> Tuple[bool, str]:
        """Genera un QR estándar, espera el escaneo del teléfono y conecta por mDNS."""
        from .wireless_qr_service import WirelessQrError, WirelessQrPairingService

        service = qr_service or WirelessQrPairingService()
        payload = qr_payload or service.create_payload()
        # La UI puede obtener el payload/imagen mediante ``create_qr_payload``;
        # este método se encarga del flujo completo para consumidores no-UI.
        try:
            result = service.pair(payload, timeout=timeout, progress=progress)
            endpoint = result.endpoint
            if not endpoint:
                services = self.find_wireless_devices()
                matching = [item for item in services if item.get("service_name", "").startswith("_adb-tls-connect")]
                endpoint = matching[0]["endpoint"] if len(matching) == 1 else None
            if not endpoint:
                return False, "Emparejamiento correcto, pero no se encontró el endpoint de conexión mDNS."
            client = get_adb_client()
            connect_output = client.connect(endpoint, timeout=12.0).strip()
            if not self._endpoint_ready(client, endpoint):
                return False, f"OFFLINE: ADB no dejó {endpoint} en estado device ({connect_output})."
            physical_hash = self._connected_identity_hash(client, endpoint)
            if not physical_hash:
                return False, f"SERIAL_NO_COINCIDE: no se verificó la identidad de {endpoint}."
            store = read_devices_store(self.devices_json)
            devices = store.setdefault("Devices", [])
            device_id = uuid.uuid4().hex
            record = {
                "Id": device_id,
                "FriendlyName": friendly_name.strip() or f"Android Wireless ({endpoint})",
                "UsbSerial": None,
                "MdnsServiceName": result.service_name,
                "Mode": "wireless-debugging-tls",
                "IsPaired": True,
                "NetworkProfiles": [],
            }
            self._record_successful_endpoint(record, endpoint, source="mdns", mdns_service_name=result.service_name,
                                             physical_id_hash=physical_hash)
            devices.append(record)
            store["CurrentDeviceId"] = device_id
            save_devices_store(store, self.devices_json)
            self.serial = endpoint
            self._explicitly_selected = True
            return True, f"Conectado por QR a {endpoint} ({connect_output})."
        except WirelessQrError as exc:
            return False, str(exc)
        except Exception as exc:
            logger.warning("No se pudo configurar Wireless Debugging mediante QR: %s", exc)
            return False, f"No se pudo configurar Wireless Debugging mediante QR: {exc}"

    def create_qr_payload(self):
        """Crea un payload QR para la UI y conserva la sesión que lo completa."""
        from .wireless_qr_service import WirelessQrPairingService
        service = WirelessQrPairingService()
        return service, service.create_payload()

    def connect_endpoint(self, endpoint: str, friendly_name: str = "") -> Tuple[bool, str]:
        """Conecta manualmente a una IP:Puerto y lo guarda."""
        ep = endpoint.strip()
        if not ep:
            return False, "Debes especificar una dirección IP o IP:Puerto."
        if ":" not in ep:
            ep = f"{ep}:5555"

        try:
            client = get_adb_client()
            logger.info("Conectando a %s...", ep)
            res = client.connect(ep, timeout=10.0).strip()
            if "failed" in res.lower() or "cannot" in res.lower() or "refused" in res.lower():
                return False, f"Error al conectar: {res}"
            if not self._endpoint_ready(client, ep):
                return False, f"OFFLINE: ADB aceptó {ep}, pero no llegó a estado device."

            time.sleep(1.0)
            model = "Android Phone"
            try:
                dev = client.device(ep)
                model = dev.getprop("ro.product.model") or model
            except Exception:
                pass

            name = friendly_name.strip() or f"{model} ({ep})"
            physical_hash = self._connected_identity_hash(client, ep)
            if not physical_hash:
                return False, f"SERIAL_NO_COINCIDE: no se verificó la identidad de {ep}."
            self._save_wifi_device_to_store(model=name, usb_serial="", endpoint=ep,
                                            physical_id_hash=physical_hash)
            self.serial = ep
            self._explicitly_selected = True
            return True, f"¡Conectado exitosamente a {ep}! Guardado como '{name}'."
        except Exception as exc:
            return False, f"Fallo al conectar con {ep}: {exc}"

    def reconnect(self) -> str:
        """Intenta reconectar el dispositivo actual vía reconexión inteligente."""
        store = read_devices_store(self.devices_json)
        current_id = store.get("CurrentDeviceId")
        if current_id:
            res = self.connect_device(current_id)
            if res.success:
                return res.message
            return f"Error en reconexión: {res.error}"

        if not self.serial:
            return "No hay ningún dispositivo seleccionado."
        if not is_network_serial(self.serial):
            return f"El dispositivo {self.serial} es USB. Si no responde, reconecta el cable."
        try:
            msg = get_adb_client().connect(self.serial, timeout=10.0).strip()
            logger.info("Reconexión a %s -> %s", self.serial, msg)
            return msg
        except Exception as exc:
            return f"Error al reconectar: {exc}"

    def reconnect_device(self, stale_serial: Optional[str] = None) -> Optional[str]:
        """Reconecta el registro dueño de un serial y devuelve su endpoint vigente.

        Se usa por operaciones largas (pull/push) cuando ADB informa que el
        puerto con el que empezó el trabajo ya está ``offline``.  Los puertos
        TLS de Wireless Debugging son efímeros, por lo que reconectar el texto
        del serial anterior no es suficiente.
        """
        store = read_devices_store(self.devices_json)
        current_id = store.get("CurrentDeviceId")
        selected_id = None if stale_serial else current_id
        if stale_serial:
            for record in store.get("Devices") or []:
                endpoints = [record.get("LastEndpoint"), record.get("UsbSerial")]
                endpoints.extend(
                    entry.get("Endpoint") if isinstance(entry, dict) else entry
                    for entry in record.get("Endpoints") or []
                )
                if stale_serial in endpoints:
                    selected_id = record.get("Id")
                    break
        if not selected_id:
            return None
        result = self.connect_device(selected_id, activate=True)
        return result.endpoint if result.success else None

    def discover_and_reconnect_wireless(self, endpoint: Optional[str] = None) -> ConnectionResult:
        """Busca el nuevo endpoint mDNS o manual y lo conecta."""
        try:
            client = get_adb_client()
            if endpoint:
                target_ep = endpoint.strip()
                res = client.connect(target_ep, timeout=10.0).strip()
                if "connected to" not in res.lower() and "already connected" not in res.lower():
                    return ConnectionResult(False, endpoint=target_ep, message=f"ADB no pudo conectar a {target_ep}: {res}", error=res)
                if not self._endpoint_ready(client, target_ep):
                    return ConnectionResult(False, endpoint=target_ep, error="OFFLINE", message="ADB aceptó el endpoint, pero no quedó en estado device.")
                self.serial = target_ep
                self._explicitly_selected = True
                self._notify_device_change()
                return ConnectionResult(True, endpoint=target_ep, message=f"Conectado a {target_ep}.")

            services = self.find_wireless_devices()
            if not services:
                return ConnectionResult(False, message="No se encontraron servicios Wireless Debugging por mDNS en esta red.")
            store = read_devices_store(self.devices_json)
            current_id = store.get("CurrentDeviceId")
            record = next((d for d in store.get("Devices", []) if d.get("Id") == current_id), None)

            preferred_service = str((record or {}).get("MdnsServiceName") or "").lower()
            matching = [item for item in services if preferred_service and item["service_name"].lower() == preferred_service]
            if record and not matching:
                return ConnectionResult(False, error="SERIAL_NO_COINCIDE", message="mDNS no identificó el servicio guardado de este teléfono.")
            selected = matching[0] if matching else services[0]

            res = client.connect(selected["endpoint"], timeout=10.0).strip()
            if "connected to" not in res.lower() and "already connected" not in res.lower():
                return ConnectionResult(False, endpoint=selected["endpoint"], message=f"ADB no pudo conectar a {selected['endpoint']}: {res}", error=res)
            if not self._endpoint_ready(client, selected["endpoint"]):
                return ConnectionResult(False, endpoint=selected["endpoint"], error="OFFLINE", message="ADB no dejó el endpoint en estado device.")
            if record:
                state, physical_hash = self._verify_reconnect_identity(client, selected["endpoint"], record)
                if state not in ("verified", "provisional"):
                    return ConnectionResult(False, endpoint=selected["endpoint"], error=state,
                                            message="El endpoint mDNS no coincide con el teléfono guardado.")
                if state == "verified":
                    self._record_successful_endpoint(record, selected["endpoint"], source="mdns",
                                                     mdns_service_name=selected["service_name"], physical_id_hash=physical_hash)
                    save_devices_store(store, self.devices_json)
                    self._provisional_endpoints.pop(str(record["Id"]), None)
                else:
                    self._provisional_endpoints[str(record["Id"])] = selected["endpoint"]
                self.serial = selected["endpoint"]
                self._explicitly_selected = True
                self._notify_device_change()
                return ConnectionResult(True, device_id=str(record["Id"]), endpoint=selected["endpoint"], method="mdns",
                                        provisional=state == "provisional",
                                        message=(f"Conexión provisional a {selected['endpoint']}; identidad pendiente de confirmar."
                                                 if state == "provisional" else f"Conectado por Wireless Debugging a {selected['endpoint']}."))
            self.serial = selected["endpoint"]
            self._explicitly_selected = True
            self._notify_device_change()
            return ConnectionResult(True, endpoint=selected["endpoint"], method="mdns", message=f"Conectado por Wireless Debugging a {selected['endpoint']}.")
        except Exception as exc:
            return ConnectionResult(False, message=f"No se pudo buscar Wireless Debugging: {exc}", error=str(exc))

    def ping_device(self, endpoint_or_ip: str) -> Tuple[bool, str]:
        """Realiza un ping ICMP a la dirección IP del dispositivo."""
        host = endpoint_or_ip.strip()
        if ":" in host:
            host = host.split(":")[0].strip("[]")
        if not host:
            return False, "Dirección IP vacía o inválida."
        try:
            cmd = ["ping", "-n", "2", "-w", "1500", host]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
            out = res.stdout or ""
            if "Respuesta desde" in out or "bytes=" in out or "Reply from" in out:
                m = re.search(r"(?:Media|Average)\s*=\s*(\d+ms)", out)
                avg = f" ({m.group(1)})" if m else ""
                return True, f"Ping exitoso a {host}{avg}. El dispositivo responde en la red local."
            return False, f"Sin respuesta de ping desde {host}. Comprueba que esté en la misma red Wi-Fi."
        except Exception as exc:
            return False, f"Error al ejecutar ping: {exc}"

    def restart_adb(self) -> Tuple[bool, str]:
        """Reinicia el servidor ADB de forma limpia."""
        try:
            ensure_single_adb_server(force=True)
            self._model_cache.clear()
            return True, "Servidor ADB reiniciado correctamente en el puerto 5037."
        except Exception as exc:
            return False, f"Fallo al reiniciar servidor ADB: {exc}"

    def get_device_full_specs(self, serial: Optional[str] = None) -> Dict[str, str]:
        """Obtiene especificaciones completas de hardware y software del dispositivo."""
        target_serial = serial or self.serial
        specs = {
            "Serial / Endpoint": target_serial or "—",
            "Modelo": "—",
            "Fabricante": "—",
            "Versión Android": "—",
            "Nivel API (SDK)": "—",
            "Arquitectura CPU": "—",
            "Serial de Fábrica": "—",
            "Dirección IP Wi-Fi": "—",
            "Batería": "—",
        }
        if not target_serial:
            return specs
        try:
            client = get_adb_client()
            dev = client.device(target_serial)
            if dev.get_state() != "device":
                specs["Estado"] = f"No disponible ({dev.get_state()})"
                return specs

            specs["Modelo"] = dev.getprop("ro.product.model") or "—"
            specs["Fabricante"] = (dev.getprop("ro.product.manufacturer") or dev.getprop("ro.product.brand") or "—").capitalize()
            specs["Versión Android"] = dev.getprop("ro.build.version.release") or "—"
            specs["Nivel API (SDK)"] = dev.getprop("ro.build.version.sdk") or "—"
            specs["Arquitectura CPU"] = dev.getprop("ro.product.cpu.abi") or "—"
            specs["Serial de Fábrica"] = dev.getprop("ro.serialno") or "—"

            try:
                ip = dev.wlan_ip()
                if ip:
                    specs["Dirección IP Wi-Fi"] = ip
            except Exception:
                pass

            bat = self._get_battery_level(dev)
            if bat is not None:
                specs["Batería"] = f"{bat}%"
        except Exception as exc:
            logger.debug("Error obteniendo specs de %s: %s", target_serial, exc)

        return specs

    @staticmethod
    def _get_battery_level(dev: Any) -> Optional[int]:
        try:
            dump = dev.shell("dumpsys battery") or ""
            match = re.search(r"level:\s*(\d+)", dump)
            return int(match.group(1)) if match else None
        except Exception:
            return None

    def run_technical_diagnostics(self) -> TechnicalDiagnosticsReport:
        """Ejecuta un diagnóstico técnico exhaustivo de ADB, red, conectividad y hardware.

        Cubre los 12 puntos de diagnóstico técnico:
        1. Versión de ADB.
        2. Ruta del ejecutable usado.
        3. Estado del servidor ADB.
        4. Dispositivos detectados.
        5. Estado de autorización.
        6. Estado de mDNS.
        7. IP de la PC.
        8. IP del dispositivo.
        9. Puertos utilizados.
        10. Prueba de conectividad.
        11. Versión de Android.
        12. Espacio libre y batería.
        """
        report = TechnicalDiagnosticsReport()
        recs = report.recommendations
        report.last_reconnect_issue = self._latest_reconnect_issue()
        issue_code = report.last_reconnect_issue.get("code")
        issue_advice = {
            "AUTH_FAILED": "La autenticación ADB falló: vuelve a emparejar el teléfono.",
            "NO_ROUTE_VPN": "No hay ruta al teléfono: revisa VPN, subred y aislamiento Wi-Fi.",
            "CONNECT_RECHAZADO": "El puerto rechazó la conexión: comprueba el puerto actual de Wireless Debugging.",
            "SERIAL_NO_COINCIDE": "El endpoint pertenece a otro teléfono o no puede verificarse; no lo asocies al registro.",
            "OFFLINE": "El endpoint quedó offline: prueba reconexión dirigida o revisa la autorización.",
        }
        if issue_code in issue_advice:
            recs.append(issue_advice[issue_code])

        # 1 & 2. Ruta y Versión de ADB
        try:
            exe = get_adb_executable()
            report.adb_path = str(exe) if exe else "No localizado en el sistema"
            if exe and os.path.isfile(exe):
                res = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=5.0)
                v_out = (res.stdout or "").strip()
                lines = v_out.splitlines()
                report.adb_version = lines[0] if lines else "ADB ejecutable presente"
            else:
                report.adb_version = "ADB no encontrado"
                recs.append("Instala o configura Android SDK Platform-Tools para tener adb disponible.")
        except Exception as exc:
            report.adb_version = f"Error al consultar versión: {exc}"
            recs.append("Comprueba los permisos del ejecutable ADB.")

        # 3. Estado del servidor ADB y puerto local 5037
        try:
            client = get_adb_client()
            s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s_sock.settimeout(1.0)
            open_code = s_sock.connect_ex(("127.0.0.1", 5037))
            s_sock.close()
            if open_code == 0:
                report.adb_server_owner = self._adb_server_owner()
                owner_path = str(report.adb_server_owner.get("path") or "")
                if owner_path and report.adb_path not in ("No localizado en el sistema", "") and os.path.normcase(owner_path) != os.path.normcase(report.adb_path):
                    recs.append(f"El puerto 5037 pertenece a otro ejecutable ({owner_path}); revisa un posible conflicto de versiones ADB.")
                try:
                    s_ver = client.server_version()
                    report.adb_server_status = f"En ejecución (Versión de protocolo: {s_ver}, Puerto: 5037)"
                except Exception:
                    report.adb_server_status = "Puerto 5037 abierto, pero cliente no pudo negociar versión"
            else:
                report.adb_server_status = "Servidor detenido (puerto 5037 cerrado)"
                recs.append("Reinicia el servidor ADB con 'adb start-server' o desde la opción de reinicio.")
        except Exception as exc:
            report.adb_server_status = f"Error consultando servidor ADB: {exc}"

        # 4 & 5. Dispositivos detectados y Estado de autorización
        try:
            client = get_adb_client()
            dev_list = client.device_list()
            for d in dev_list:
                st = "desconocido"
                try:
                    st = d.get_state()
                except Exception:
                    pass
                transport = "network" if is_network_serial(d.serial) else "usb"
                report.detected_devices.append({
                    "serial": d.serial,
                    "state": st,
                    "transport": transport,
                })

            if not dev_list:
                recs.append("No se detectaron dispositivos por USB o Wi-Fi. Conecta un cable o empareja por Wireless Debugging.")
        except Exception as exc:
            logger.debug("Error listando dispositivos en diagnóstico: %s", exc)

        report.selected_serial = self.serial
        target_serial = self.serial
        dev_obj: Any = None
        if target_serial:
            try:
                client = get_adb_client()
                dev_obj = client.device(target_serial)
                state = dev_obj.get_state()
                report.authorization_status = state
                if state == "unauthorized":
                    recs.append(f"El dispositivo {target_serial} está NO AUTORIZADO. Acepta la solicitud de depuración USB en su pantalla.")
                elif state == "offline":
                    recs.append(f"El dispositivo {target_serial} está OFFLINE. Desconecta y vuelve a conectar o reinicia el servidor ADB.")
            except Exception as exc:
                report.authorization_status = f"No disponible ({exc})"
        else:
            report.authorization_status = "Ningún dispositivo seleccionado"
            if report.detected_devices:
                recs.append("Hay dispositivos detectados pero ninguno está seleccionado. Selecciona uno en el panel lateral.")

        # 6. Estado de mDNS
        try:
            mdns_services = self.find_wireless_devices()
            report.mdns_services = mdns_services
            if mdns_services:
                report.mdns_status = f"Activo ({len(mdns_services)} servicio(s) detectado(s))"
            else:
                report.mdns_status = "Sin servicios detectados"
                recs.append("mDNS no anunció un endpoint TLS; comprueba Wireless Debugging, la red local o introduce el endpoint manualmente.")
        except Exception as exc:
            report.mdns_status = f"Error al consultar mDNS: {exc}"

        # 7. IP de la PC
        try:
            host_name = socket.gethostname()
            host_ip = socket.gethostbyname(host_name)
            ips = set()
            try:
                for item in socket.getaddrinfo(host_name, None, socket.AF_INET):
                    ip_found = item[4][0]
                    if not ip_found.startswith("127."):
                        ips.add(ip_found)
            except Exception:
                pass
            if not ips and host_ip:
                ips.add(host_ip)
            report.pc_all_ips = sorted(list(ips))
            report.pc_ip = host_ip or (report.pc_all_ips[0] if report.pc_all_ips else "127.0.0.1")
        except Exception as exc:
            report.pc_ip = f"Error resolviendo IP local: {exc}"

        # 8, 9, 10, 11, 12: IP del dispositivo, puertos, conectividad, Android, espacio y batería
        phone_ip: Optional[str] = None
        device_port = 5555
        if target_serial and is_network_serial(target_serial):
            parts = target_serial.split(":")
            phone_ip = parts[0].strip("[]")
            if len(parts) > 1 and parts[1].isdigit():
                device_port = int(parts[1])

        # Consultar propiedades si el dispositivo está disponible y autorizado
        if dev_obj and report.authorization_status == "device":
            try:
                if not phone_ip:
                    wlan = dev_obj.wlan_ip()
                    if wlan:
                        phone_ip = wlan
            except Exception:
                pass

            try:
                report.device_model = dev_obj.getprop("ro.product.model") or "—"
                report.device_manufacturer = (dev_obj.getprop("ro.product.manufacturer") or dev_obj.getprop("ro.product.brand") or "—").capitalize()
                report.android_version = dev_obj.getprop("ro.build.version.release") or "—"
                report.sdk_api = dev_obj.getprop("ro.build.version.sdk") or "—"
                report.cpu_abi = dev_obj.getprop("ro.product.cpu.abi") or "—"
            except Exception as exc:
                logger.debug("Error leyendo props en diagnóstico: %s", exc)

            # Batería
            try:
                dump_bat = dev_obj.shell("dumpsys battery") or ""
                m_lvl = re.search(r"level:\s*(\d+)", dump_bat)
                if m_lvl:
                    report.battery_level = int(m_lvl.group(1))
                m_st = re.search(r"status:\s*(\d+)", dump_bat)
                if m_st:
                    st_code = int(m_st.group(1))
                    status_map = {1: "Desconocido", 2: "Cargando", 3: "Descargando", 4: "No cargando", 5: "Llena"}
                    report.battery_status = status_map.get(st_code, f"Código {st_code}")
                if report.battery_level is not None and report.battery_level < 20:
                    recs.append(f"La batería del teléfono está baja ({report.battery_level}%). Conéctalo a la corriente.")
            except Exception:
                pass

            # Almacenamiento teléfono (/sdcard)
            try:
                df_out = dev_obj.shell("df -h /sdcard") or ""
                lines = [ln.strip() for ln in df_out.splitlines() if ln.strip()]
                if len(lines) > 1:
                    report.storage_phone = lines[-1]
                else:
                    report.storage_phone = df_out.strip() or "—"
            except Exception as exc:
                report.storage_phone = f"No disponible ({exc})"

        report.device_ip = phone_ip

        # 9. Puertos utilizados
        report.ports_used = {
            "adb_server_port": 5037,
            "device_port": device_port if (target_serial and is_network_serial(target_serial)) else "USB / N/A",
            "is_tls": True if (device_port != 5555 and is_network_serial(target_serial or "")) else False,
        }

        # 10. Prueba de conectividad (Ping ICMP y Socket TCP si hay IP)
        if phone_ip:
            ping_ok, ping_msg = self.ping_device(phone_ip)
            tcp_ok = False
            tcp_err = ""
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                code = sock.connect_ex((phone_ip, device_port))
                sock.close()
                tcp_ok = (code == 0)
                if not tcp_ok:
                    tcp_err = f"Código error socket: {code}"
            except Exception as exc:
                tcp_err = str(exc)

            report.connectivity_test = {
                "tested_host": phone_ip,
                "tested_port": device_port,
                "icmp_ping_ok": ping_ok,
                "icmp_ping_message": ping_msg,
                "tcp_port_open": tcp_ok,
                "tcp_error": tcp_err,
            }
            if not ping_ok and not tcp_ok:
                recs.append(f"No hay respuesta de ping hacia {phone_ip}. Comprueba que la PC y el móvil estén en la misma red Wi-Fi o sin aislamiento de cliente (AP isolation).")
            if not tcp_ok and is_network_serial(target_serial or ""):
                recs.append(f"El puerto ADB {device_port} en {phone_ip} no responde. Revisa si la depuración inalámbrica cambió de puerto o se desactivó.")
        else:
            report.connectivity_test = {
                "tested_host": None,
                "status": "No se determinó dirección IP para prueba de red (dispositivo no conectado o solo USB)",
            }

        # Almacenamiento local PC
        try:
            total_b, used_b, free_b = shutil.disk_usage(".")
            report.storage_pc = f"{free_b / (1024**3):.1f} GB libres de {total_b / (1024**3):.1f} GB"
        except Exception:
            report.storage_pc = "No disponible"

        if not recs:
            recs.append("Todos los servicios y diagnósticos básicos operan de forma correcta.")

        return report

    @staticmethod
    def _adb_server_owner() -> Dict[str, Any]:
        """Consulta el proceso que escucha en 5037 sin alterar el servidor."""
        if os.name != "nt":
            return {}
        command = (
            "$c=Get-NetTCPConnection -LocalPort 5037 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if($c){$p=Get-CimInstance Win32_Process -Filter (\"ProcessId=\"+$c.OwningProcess) -ErrorAction SilentlyContinue; "
            "[pscustomobject]@{pid=$c.OwningProcess;name=$p.Name;path=$p.ExecutablePath} | ConvertTo-Json -Compress}"
        )
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                                    capture_output=True, text=True, timeout=5, check=False)
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}

    def _latest_reconnect_issue(self) -> Dict[str, Any]:
        path = self.devices_json.parent / "reconnect-attempts.jsonl"
        try:
            current_id = read_devices_store(self.devices_json).get("CurrentDeviceId")
            for line in reversed(path.read_text(encoding="utf-8").splitlines()[-100:]):
                entry = json.loads(line)
                if current_id and entry.get("device_id") != current_id:
                    continue
                if entry.get("code") in ("SUCCESS", "VERIFIED", "PROVISIONAL"):
                    return {}
                if entry.get("code") not in ("START", "CONNECTED"):
                    return entry
        except (OSError, ValueError):
            pass
        return {}

    def run_network_diagnostics(self) -> TechnicalDiagnosticsReport:
        """Alias retrocompatible para ejecutar diagnóstico técnico completo."""
        return self.run_technical_diagnostics()
