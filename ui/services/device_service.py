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
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Endpoint": self.endpoint,
            "ConnectedUtc": self.connected_utc,
            "MdnsServiceName": self.mdns_service_name,
            "Source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EndpointRecord:
        return cls(
            endpoint=data.get("Endpoint") or data.get("endpoint") or "",
            connected_utc=data.get("ConnectedUtc") or data.get("connected_utc") or "",
            mdns_service_name=data.get("MdnsServiceName") or data.get("mdns_service_name"),
            source=data.get("Source") or data.get("source"),
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
        path.parent.mkdir(parents=True, exist_ok=True)
        data["UpdatedUtc"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("Error al guardar devices.json: %s", exc)


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

    def __init__(self, serial: Optional[str] = None, devices_json_path: Path = DEVICES_JSON) -> None:
        self.devices_json = devices_json_path
        ensure_devices_store_migrated(canonical_path=self.devices_json)
        self.serial: Optional[str] = serial or resolve_serial_from_config(self.devices_json)
        self._explicitly_selected: bool = bool(serial)
        self._model_cache: Dict[str, str] = {}
        self.auto_wifi_enabled: bool = True
        self._auto_converted_serials: Set[str] = set()
        self._converting_lock = threading.Lock()
        self._supervisor_stop_callbacks: Dict[str, Callable[[], None]] = {}
        self._supervisor_restart_callbacks: Dict[str, Callable[[], None]] = {}
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_device_id: Optional[str] = None
        self._watchdog_policy = RetryPolicy()
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

    def connect_device(self, device_id: str) -> ConnectionResult:
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
        raw_list = client.list()

        # 1. Endpoint mDNS actual
        try:
            services = self.find_wireless_devices()
            preferred_service = (target.get("MdnsServiceName") or "").lower()
            last_ip = target.get("LastIp") or ""
            matched_svc = None
            if preferred_service:
                matched_svc = next((s for s in services if s["service_name"].lower() == preferred_service), None)
            if not matched_svc and last_ip:
                matched_svc = next((s for s in services if s["host"].lower() == last_ip.lower()), None)

            if matched_svc:
                ep = matched_svc["endpoint"]
                logger.info("Probando reconexión mDNS a %s para %s...", ep, target.get("FriendlyName"))
                conn_res = client.connect(ep, timeout=8.0).strip()
                if "connected to" in conn_res.lower() or "already connected" in conn_res.lower():
                    self._record_successful_endpoint(target, ep, source="mdns", mdns_service_name=matched_svc["service_name"])
                    save_devices_store(store, self.devices_json)
                    self.serial = ep
                    self._explicitly_selected = True
                    self.refresh_device_metadata(device_id)
                    return ConnectionResult(
                        success=True,
                        device_id=device_id,
                        endpoint=ep,
                        method="mdns",
                        message=f"Conectado exitosamente vía mDNS a {ep}.",
                    )
        except Exception as exc:
            logger.debug("Intento mDNS no disponible: %s", exc)

        # 2. Endpoint guardado
        saved_ep = target.get("LastEndpoint")
        if saved_ep and is_network_serial(saved_ep):
            try:
                logger.info("Probando endpoint guardado %s...", saved_ep)
                conn_res = client.connect(saved_ep, timeout=6.0).strip()
                if "connected to" in conn_res.lower() or "already connected" in conn_res.lower():
                    self._record_successful_endpoint(target, saved_ep, source="saved")
                    save_devices_store(store, self.devices_json)
                    self.serial = saved_ep
                    self._explicitly_selected = True
                    self.refresh_device_metadata(device_id)
                    return ConnectionResult(
                        success=True,
                        device_id=device_id,
                        endpoint=saved_ep,
                        method="saved",
                        message=f"Conectado a endpoint guardado {saved_ep}.",
                    )
            except Exception as exc:
                logger.debug("Fallo al conectar endpoint guardado %s: %s", saved_ep, exc)

        # 3. Historial de endpoints
        endpoints_history = target.get("Endpoints") or []
        for ep_entry in endpoints_history:
            ep = ep_entry.get("Endpoint") if isinstance(ep_entry, dict) else ep_entry
            if ep and ep != saved_ep and is_network_serial(ep):
                try:
                    logger.info("Probando endpoint de historial %s...", ep)
                    conn_res = client.connect(ep, timeout=5.0).strip()
                    if "connected to" in conn_res.lower() or "already connected" in conn_res.lower():
                        self._record_successful_endpoint(target, ep, source="history")
                        save_devices_store(store, self.devices_json)
                        self.serial = ep
                        self._explicitly_selected = True
                        self.refresh_device_metadata(device_id)
                        return ConnectionResult(
                            success=True,
                            device_id=device_id,
                            endpoint=ep,
                            method="history",
                            message=f"Conectado mediante historial de endpoints a {ep}.",
                        )
                except Exception as exc:
                    logger.debug("Fallo al probar historial %s: %s", ep, exc)

        # 4. Endpoint privado o VPN (NetworkProfiles)
        profiles = target.get("NetworkProfiles") or []
        for prof in profiles:
            pep = prof.get("PreferredEndpoint")
            if pep and is_network_serial(pep) and pep not in (saved_ep,):
                try:
                    logger.info("Probando endpoint de perfil VPN/Red %s...", pep)
                    conn_res = client.connect(pep, timeout=5.0).strip()
                    if "connected to" in conn_res.lower() or "already connected" in conn_res.lower():
                        self._record_successful_endpoint(target, pep, source="profile")
                        save_devices_store(store, self.devices_json)
                        self.serial = pep
                        self._explicitly_selected = True
                        self.refresh_device_metadata(device_id)
                        return ConnectionResult(
                            success=True,
                            device_id=device_id,
                            endpoint=pep,
                            method="profile",
                            message=f"Conectado mediante perfil de red a {pep}.",
                        )
                except Exception as exc:
                    logger.debug("Fallo al probar perfil %s: %s", pep, exc)

        # 5. USB como respaldo
        usb_serial = target.get("UsbSerial")
        if usb_serial:
            usb_online = next((d for d in raw_list if d.serial == usb_serial and d.state == "device"), None)
            if usb_online:
                self.serial = usb_serial
                self._explicitly_selected = True
                now_utc = datetime.now(timezone.utc).isoformat()
                target["LastConnectedUtc"] = now_utc
                target["LastSeenUtc"] = now_utc
                save_devices_store(store, self.devices_json)
                self.refresh_device_metadata(device_id)
                return ConnectionResult(
                    success=True,
                    device_id=device_id,
                    endpoint=usb_serial,
                    method="usb",
                    message=f"Conectado por respaldo USB ({usb_serial}).",
                )

        return ConnectionResult(
            success=False,
            device_id=device_id,
            error="No fue posible conectar por mDNS, endpoint guardado, historial, VPN ni USB.",
            message="Dispositivo no localizable en red ni por USB.",
        )

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
    ) -> None:
        """Agrega un endpoint al historial (conservando hasta 10) y actualiza metadatos."""
        now_utc = datetime.now(timezone.utc).isoformat()
        record["LastEndpoint"] = endpoint
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
            },
        )
        del endpoints_list[10:]

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

            ep_online = online_map.get(last_ep) if last_ep else None
            usb_online = online_map.get(usb) if usb else None

            active_info = ep_online or usb_online
            if active_info:
                if ep_online:
                    matched_online_serials.add(last_ep)
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
            is_active = (self.serial in (last_ep, usb) and bool(self.serial))

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

    def set_active_by_saved_id(self, device_id: str) -> Tuple[bool, str]:
        """Activa un dispositivo guardado reconectándolo si es necesario."""
        res = self.connect_device(device_id)
        if res.success:
            self.serial = res.endpoint
            self._explicitly_selected = True
            store = read_devices_store(self.devices_json)
            store["CurrentDeviceId"] = device_id
            save_devices_store(store, self.devices_json)
            return True, res.message
        return False, res.error or res.message

    # -------------------------------------------------------------------------
    # Auto-conversión USB a Wi-Fi y utilidades
    # -------------------------------------------------------------------------
    def init_adb(self) -> None:
        """Arranca o verifica el daemon ADB."""
        ensure_single_adb_server(force=False)

    def check_and_auto_convert_usb(self, on_result: Optional[Callable[[bool, str], None]] = None) -> None:
        """Inspecciona si hay un dispositivo USB listo para auto-conversión a Wi-Fi."""
        if not self.auto_wifi_enabled:
            return

        if not self._converting_lock.acquire(blocking=False):
            return

        launched_worker = False
        try:
            client = get_adb_client()
            available = client.list()

            usb_candidates = [
                d for d in available
                if d.state == "device"
                and not is_network_serial(d.serial)
                and d.serial not in self._auto_converted_serials
            ]

            if not usb_candidates:
                return

            usb_target = usb_candidates[0].serial
            saved_devices = read_devices_store(self.devices_json).get("Devices") or []
            modern_saved = next(
                (
                    item for item in saved_devices
                    if item.get("UsbSerial") == usb_target
                    and item.get("Mode") == "wireless-debugging-tls"
                ),
                None,
            )
            if modern_saved is not None:
                self._auto_converted_serials.add(usb_target)
                logger.info("Wireless Debugging moderno ya configurado para %s; USB se mantiene como respaldo.", usb_target)
                return

            has_wifi_active = any(d.state == "device" and is_network_serial(d.serial) for d in available)
            if has_wifi_active:
                self._auto_converted_serials.add(usb_target)
                return

            logger.info("⚡ Dispositivo USB detectado (%s). Iniciando auto-conversión a Wi-Fi...", usb_target)
            self._auto_converted_serials.add(usb_target)

            def worker():
                try:
                    ok, msg = self.convert_usb_to_wifi(usb_serial=usb_target)
                    if ok:
                        logger.info("⚡ %s", msg)
                    else:
                        logger.warning("⚡ Auto-conversión a Wi-Fi no completada: %s", msg)
                    if on_result:
                        on_result(ok, msg)
                finally:
                    self._converting_lock.release()

            launched_worker = True
            threading.Thread(target=worker, name="auto-wifi-worker", daemon=True).start()

        except Exception as exc:
            logger.debug("Aviso en escáner de auto-wifi: %s", exc)
        finally:
            if not launched_worker:
                self._converting_lock.release()

    def convert_usb_to_wifi(self, usb_serial: Optional[str] = None) -> Tuple[bool, str]:
        """Convierte una conexión USB activa a Wi-Fi (tcpip 5555)."""
        serial = usb_serial or self.serial
        if not serial:
            return False, "No se especificó dispositivo USB."
        if is_network_serial(serial):
            return False, f"{serial} ya es una conexión de red (Wi-Fi)."

        try:
            client = get_adb_client()
            dev = client.device(serial)
            state = dev.get_state()
            if state == "unauthorized":
                return False, "El celular requiere autorización en pantalla antes de activar Wi-Fi."
            if state != "device":
                return False, f"El dispositivo no está listo (estado: {state})."

            logger.info("Obteniendo dirección IP Wi-Fi de %s sobre canal USB activo...", serial)
            model = dev.getprop("ro.product.model") or "Android Phone"

            ip = None
            try:
                ip = dev.wlan_ip()
            except Exception:
                pass

            if not ip or ip == "127.0.0.1":
                out = dev.shell("ip -4 addr show wlan0") or ""
                m = re.search(r"inet\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", out)
                ip = m.group(1) if m else None

            if not ip or ip == "127.0.0.1":
                return False, "No se pudo obtener la IP Wi-Fi de 'wlan0'. Asegúrate de que el celular esté conectado a la misma red Wi-Fi."

            endpoint = f"{ip}:5555"
            logger.info("IP Wi-Fi detectada: %s. Activando TCP/IP en puerto 5555...", ip)

            try:
                dev.tcpip(5555)
            except Exception as tcp_exc:
                logger.debug("Respuesta en comando tcpip: %s", tcp_exc)

            time.sleep(2.0)
            conn_res = client.connect(endpoint, timeout=12.0)
            if "connected to" not in conn_res.lower() and "already connected" not in conn_res.lower():
                time.sleep(1.0)
                conn_res = client.connect(endpoint, timeout=12.0)

            self._save_wifi_device_to_store(model=model, usb_serial=serial, endpoint=endpoint)
            self.serial = endpoint
            self._explicitly_selected = True
            self._auto_converted_serials.add(serial)
            return True, f"¡Conexión Wi-Fi establecida con éxito a {endpoint}! Ya puedes desconectar el cable USB."
        except Exception as exc:
            logger.error("Error al convertir USB a Wi-Fi: %s", exc)
            return False, f"Fallo al convertir a Wi-Fi: {exc}"

    def _save_wifi_device_to_store(self, model: str, usb_serial: str, endpoint: str) -> None:
        """Persiste dispositivo Wi-Fi en devices.json."""
        store = read_devices_store(self.devices_json)
        devices = store.setdefault("Devices", [])
        existing = next(
            (d for d in devices if d.get("UsbSerial") == usb_serial or d.get("LastEndpoint") == endpoint),
            None,
        )
        if existing:
            self._record_successful_endpoint(existing, endpoint, source="tcpip")
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
            self._record_successful_endpoint(new_dev, endpoint, source="tcpip")
            devices.append(new_dev)

        store["CurrentDeviceId"] = device_id
        save_devices_store(store, self.devices_json)

    @staticmethod
    def _run_adb_host(*arguments: str, timeout: float = 15.0) -> str:
        """Ejecuta un comando ADB del host (pair, mdns o connect)."""
        command = [get_adb_executable(), *arguments]
        kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout, "check": False}
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
                return False, "Emparejamiento correcto, pero no se encontró el servicio mDNS."
            matching = [item for item in services if item["host"].lower() == host.lower()]
            selected = matching[0] if len(matching) == 1 else services[0] if len(services) == 1 else None
            if selected is None:
                return False, "Hay varios teléfonos mDNS; usa Buscar dispositivo WiFi para seleccionar uno."

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
            self._record_successful_endpoint(record, selected["endpoint"], source="mdns", mdns_service_name=selected["service_name"])
            devices.append(record)
            store["CurrentDeviceId"] = dev_id
            save_devices_store(store, self.devices_json)

            client = get_adb_client()
            res = client.connect(selected["endpoint"], timeout=12.0).strip()
            self.serial = selected["endpoint"]
            self._explicitly_selected = True
            return True, f"Conectado por Wireless Debugging a {selected['endpoint']} ({res})."
        except Exception as exc:
            logger.warning("No se pudo configurar Wireless Debugging: %s", exc)
            return False, f"No se pudo configurar Wireless Debugging: {exc}"

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

            time.sleep(1.0)
            model = "Android Phone"
            try:
                dev = client.device(ep)
                model = dev.getprop("ro.product.model") or model
            except Exception:
                pass

            name = friendly_name.strip() or f"{model} ({ep})"
            self._save_wifi_device_to_store(model=name, usb_serial="", endpoint=ep)
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

    def discover_and_reconnect_wireless(self, endpoint: Optional[str] = None) -> ConnectionResult:
        """Busca el nuevo endpoint mDNS o manual y lo conecta."""
        try:
            client = get_adb_client()
            if endpoint:
                target_ep = endpoint.strip()
                res = client.connect(target_ep, timeout=10.0)
                self.serial = target_ep
                self._explicitly_selected = True
                return ConnectionResult(True, endpoint=target_ep, message=f"Conectado a {target_ep}.")

            services = self.find_wireless_devices()
            if not services:
                return ConnectionResult(False, message="No se encontraron servicios Wireless Debugging por mDNS en esta red.")
            store = read_devices_store(self.devices_json)
            current_id = store.get("CurrentDeviceId")
            record = next((d for d in store.get("Devices", []) if d.get("Id") == current_id), None)

            preferred_service = str((record or {}).get("MdnsServiceName") or "").lower()
            matching = [item for item in services if preferred_service and item["service_name"].lower() == preferred_service]
            selected = matching[0] if matching else services[0]

            client.connect(selected["endpoint"], timeout=10.0)
            self.serial = selected["endpoint"]
            self._explicitly_selected = True
            if record:
                self._record_successful_endpoint(record, selected["endpoint"], source="mdns", mdns_service_name=selected["service_name"])
                save_devices_store(store, self.devices_json)
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
            if not ping_ok:
                recs.append(f"No hay respuesta de ping hacia {phone_ip}. Comprueba que la PC y el móvil estén en la misma red Wi-Fi o sin aislamiento de cliente (AP isolation).")
            elif not tcp_ok and is_network_serial(target_serial or ""):
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

    def run_network_diagnostics(self) -> TechnicalDiagnosticsReport:
        """Alias retrocompatible para ejecutar diagnóstico técnico completo."""
        return self.run_technical_diagnostics()
