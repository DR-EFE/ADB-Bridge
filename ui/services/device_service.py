"""Servicio de estado del dispositivo para la UI, basado en ``adb_manager`` y ``devices.json``.

Garantiza:
1. Consulta no invasiva mediante `client.list()` (detecta dispositivos en estado device, unauthorized y offline).
2. Detección proactiva de dispositivos no autorizados para alertar al usuario.
3. Conversión de USB a Wi-Fi AUTOMÁTICA o manual (obtiene IP primero, luego activa tcpip 5555, conecta y guarda en devices.json).
"""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import adbutils

from ui_automation.adb_manager import (
    ensure_single_adb_server,
    get_adb_executable,
    get_adb_client,
    is_network_serial,
)

logger = logging.getLogger("ui.device_service")

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEVICES_JSON = CONFIG_DIR / "devices.json"


@dataclass
class DeviceInfo:
    """Información resumida de un dispositivo ADB."""

    serial: str
    state: str  # device | unauthorized | offline
    is_network: bool
    friendly_name: Optional[str] = None


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
    available_devices: List[DeviceInfo] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.available_devices is None:
            self.available_devices = []


@dataclass
class RetryPolicy:
    """Límites del supervisor de reconexión inalámbrica."""

    initial_interval: float = 2.0
    max_interval: float = 30.0
    timeout: float = 120.0


@dataclass
class ConnectionResult:
    """Resultado estructurado de una búsqueda o reconexión ADB."""

    success: bool
    serial: Optional[str] = None
    endpoint: Optional[str] = None
    method: str = "none"
    message: str = ""
    candidates: List[Dict[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = []

    def __str__(self) -> str:
        return self.message


@dataclass
class NetworkDiagnosticReport:
    """Información de conectividad de la PC, ADB y el dispositivo."""

    adb_path: str
    adb_version: str
    adb_server_status: str
    adb_state: str
    current_serial: Optional[str]
    current_endpoint: Optional[str]
    known_endpoints: List[Dict[str, Any]]
    interfaces: List[Dict[str, str]]
    gateways: List[str]
    active_networks: List[str]
    mdns_services: List[Dict[str, str]]
    tcp_tests: List[Dict[str, Any]]
    vpn_interfaces: List[str]
    recommendations: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adb_path": self.adb_path,
            "adb_version": self.adb_version,
            "adb_server_status": self.adb_server_status,
            "adb_state": self.adb_state,
            "current_serial": self.current_serial,
            "current_endpoint": self.current_endpoint,
            "known_endpoints": self.known_endpoints,
            "interfaces": self.interfaces,
            "gateways": self.gateways,
            "active_networks": self.active_networks,
            "mdns_services": self.mdns_services,
            "tcp_tests": self.tcp_tests,
            "vpn_interfaces": self.vpn_interfaces,
            "recommendations": self.recommendations,
        }


class WatchHandle:
    """Control pequeño y seguro para detener un supervisor de reconexión."""

    def __init__(self, stop_event: threading.Event, thread: Optional[threading.Thread] = None) -> None:
        self.event = stop_event
        self.thread = thread

    def stop(self) -> None:
        self.event.set()

    def is_alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())


def read_devices_store(path: Path = DEVICES_JSON) -> Dict[str, Any]:
    """Lee el archivo devices.json si existe."""
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
    """Lee el dispositivo actual de ``config/devices.json``."""
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
    """Fachada de ADB para la UI (estado, reconexión, USB a Wi-Fi automático, serial actual)."""

    def __init__(self, serial: Optional[str] = None) -> None:
        self.serial: Optional[str] = serial or resolve_serial_from_config()
        self._model_cache: Dict[str, str] = {}
        self.auto_wifi_enabled: bool = True
        self._auto_converted_serials: Set[str] = set()
        self._converting_lock = threading.Lock()
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_device_id: Optional[str] = None
        self._watchdog_policy = RetryPolicy()
        logger.info("Serial inicial configurado: %s", self.serial or "autodetección")

    def init_adb(self) -> None:
        """Arranca o verifica el daemon ADB."""
        ensure_single_adb_server(force=False)

    def list_known_endpoints(self, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Devuelve endpoints conocidos ordenados por el último uso."""
        store = read_devices_store(DEVICES_JSON)
        target_id = device_id or store.get("CurrentDeviceId")
        record = next((item for item in store.get("Devices", []) or [] if item.get("Id") == target_id), None)
        if record is None:
            record = self._saved_record()
            if device_id:
                record = next((item for item in (read_devices_store(DEVICES_JSON).get("Devices") or []) if item.get("Id") == device_id), record)
        if record is None:
            return []
        result: List[Dict[str, Any]] = []
        for item in record.get("Endpoints", []) or []:
            if isinstance(item, str):
                item = {"Endpoint": item}
            endpoint = str(item.get("Endpoint") or item.get("endpoint") or "").strip()
            if endpoint and not any(row.get("Endpoint") == endpoint for row in result):
                result.append({"Endpoint": endpoint, "ServiceName": item.get("ServiceName") or item.get("MdnsServiceName"), "LastUsedUtc": item.get("LastUsedUtc"), "Source": item.get("Source", "history")})
        last_endpoint = str(record.get("LastEndpoint") or "").strip()
        last_row = next((row for row in result if row.get("Endpoint") == last_endpoint), None) if last_endpoint else None
        if last_row is None and last_endpoint:
            last_row = {"Endpoint": last_endpoint, "ServiceName": record.get("MdnsServiceName"), "LastUsedUtc": record.get("LastConnectedUtc"), "Source": "last"}
        result = [row for row in result if row.get("Endpoint") != last_endpoint]
        result.sort(key=lambda row: str(row.get("LastUsedUtc") or ""), reverse=True)
        return ([last_row] if last_row else []) + result

    def save_successful_endpoint(self, device_id: Optional[str], endpoint: str, service_name: str = "") -> None:
        """Agrega un endpoint exitoso al historial del dispositivo."""
        endpoint = endpoint.strip()
        if not endpoint:
            return
        store = read_devices_store(DEVICES_JSON)
        devices = store.setdefault("Devices", [])
        record = next((item for item in devices if item.get("Id") == (device_id or store.get("CurrentDeviceId"))), None) or self._saved_record()
        if record is None:
            return
        history = record.setdefault("Endpoints", [])
        history[:] = [item for item in history if (item.get("Endpoint") if isinstance(item, dict) else item) != endpoint]
        history.insert(0, {"Endpoint": endpoint, "ServiceName": service_name or record.get("MdnsServiceName"), "LastUsedUtc": datetime.now(timezone.utc).isoformat(), "Source": "successful"})
        record["Endpoints"] = history[:10]
        record["LastEndpoint"] = endpoint
        record["LastIp"] = endpoint.rsplit(":", 1)[0].strip("[]") if ":" in endpoint else endpoint
        record["LastConnectedUtc"] = datetime.now(timezone.utc).isoformat()
        store["CurrentDeviceId"] = record.get("Id")
        save_devices_store(store, DEVICES_JSON)

    def get_status(self) -> DeviceStatus:
        """
        Consulta el estado actual de los dispositivos sin reiniciar el servidor.
        Detecta correctamente dispositivos en estado device, unauthorized y offline.
        """
        status = DeviceStatus(serial=self.serial)

        try:
            client = get_adb_client()
            raw_list = client.list()
        except Exception as exc:
            status.adb_state = "daemon ADB no disponible"
            status.error = f"Error al comunicar con ADB: {exc}"
            return status

        # Mapear todos los dispositivos detectados
        available: List[DeviceInfo] = []
        for info in raw_list:
            available.append(
                DeviceInfo(
                    serial=info.serial,
                    state=info.state,
                    is_network=is_network_serial(info.serial),
                )
            )
        status.available_devices = available

        if not available:
            status.adb_state = "desconectado"
            status.error = "No hay ningún teléfono detectado por USB ni por Wi-Fi."
            return status

        # Limpiar registros de auto-conversión para dispositivos que ya no están enchufados
        current_connected_serials = {d.serial for d in available}
        self._auto_converted_serials.intersection_update(current_connected_serials)

        # Resolver cuál es el dispositivo objetivo
        # Si hay un dispositivo Wi-Fi conectado en estado 'device', SIEMPRE preferirlo sobre USB
        active_net = next((d for d in available if d.state == "device" and d.is_network), None)
        target_info = None

        if active_net:
            target_info = active_net
            if self.serial != active_net.serial:
                logger.info("Conexión Wi-Fi activa disponible (%s). Conmutando automáticamente desde %s.", active_net.serial, self.serial)
                self.serial = active_net.serial
        elif self.serial:
            target_info = next((d for d in available if d.serial == self.serial), None)

        if target_info is None:
            usb_device = next((d for d in available if d.state == "device" and not d.is_network), None)
            target_info = usb_device or available[0]
            self.serial = target_info.serial
            logger.info("Dispositivo activo seleccionado: %s (estado: %s)", self.serial, target_info.state)

        status.serial = self.serial
        status.adb_state = target_info.state

        if target_info.state == "unauthorized":
            status.connected = False
            status.error = (
                f"El dispositivo {target_info.serial} requiere AUTORIZACIÓN.\n"
                "Desbloquea la pantalla de tu celular y pulsa 'Permitir siempre la depuración USB'."
            )
            return status

        if target_info.state != "device":
            status.connected = False
            status.error = f"Dispositivo {target_info.serial} en estado '{target_info.state}'."
            return status

        # Estado 'device': consultar propiedades
        status.connected = True
        try:
            dev = client.device(self.serial)
            if self.serial not in self._model_cache:
                self._model_cache[self.serial] = dev.getprop("ro.product.model") or "Android"
            status.model = self._model_cache[self.serial]
            status.android_version = dev.getprop("ro.build.version.release") or "—"

            battery_dump = dev.shell("dumpsys battery") or ""
            match_lvl = re.search(r"level:\s*(\d+)", battery_dump)
            status.battery = int(match_lvl.group(1)) if match_lvl else None

            match_temp = re.search(r"temperature:\s*(\d+)", battery_dump)
            if match_temp:
                status.temperature = round(float(match_temp.group(1)) / 10.0, 1)

        except Exception as exc:
            status.error = str(exc)

        return status

    def check_and_auto_convert_usb(self, on_result: Optional[Callable[[bool, str], None]] = None) -> None:
        """
        Inspecciona si hay un dispositivo USB listo que aún no se haya convertido a Wi-Fi.
        Si se detecta y auto_wifi_enabled es True, ejecuta la conversión de forma automática.
        """
        if not self.auto_wifi_enabled:
            return

        if not self._converting_lock.acquire(blocking=False):
            return  # Ya hay una conversión en progreso

        launched_worker = False
        try:
            client = get_adb_client()
            available = client.list()

            # Buscar dispositivos USB en estado 'device' que no hayan sido convertidos en esta sesión
            usb_candidates = [
                d for d in available
                if d.state == "device"
                and not is_network_serial(d.serial)
                and d.serial not in self._auto_converted_serials
            ]

            if not usb_candidates:
                return

            usb_target = usb_candidates[0].serial
            # Un dispositivo ya emparejado con Wireless Debugging moderno usa
            # USB solo como respaldo; no lo conviertas otra vez a tcpip-5555.
            saved_devices = read_devices_store(DEVICES_JSON).get("Devices") or []
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
                logger.info("Wireless Debugging moderno ya configurado para %s; se conserva USB como respaldo.", usb_target)
                return
            # Verificar si ya existe una conexión Wi-Fi activa para no duplicar
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

    def _saved_record(self) -> Optional[Dict[str, Any]]:
        store = read_devices_store(DEVICES_JSON)
        devices = store.get("Devices") or []
        current_id = store.get("CurrentDeviceId")
        if current_id:
            found = next((d for d in devices if d.get("Id") == current_id), None)
            if found:
                return found
        if self.serial:
            return next((d for d in devices if d.get("LastEndpoint") == self.serial), None)
        return devices[0] if len(devices) == 1 else None

    def _connect_modern_endpoint(self, endpoint: str, record: Optional[Dict[str, Any]] = None) -> str:
        client = get_adb_client()
        result = client.connect(endpoint, timeout=12.0).strip()
        if "failed" in result.lower() or "cannot" in result.lower() or "refused" in result.lower():
            raise RuntimeError(result)
        info = next((item for item in client.list() if item.serial == endpoint and item.state == "device"), None)
        if info is None:
            raise RuntimeError(f"ADB conectó a {endpoint}, pero el dispositivo no terminó en estado device.")
        self.serial = endpoint
        self._save_wireless_device(endpoint, record=record)
        return f"Conectado por Wireless Debugging a {endpoint}."

    def _save_wireless_device(self, endpoint: str, record: Optional[Dict[str, Any]] = None) -> None:
        store = read_devices_store(DEVICES_JSON)
        devices = store.setdefault("Devices", [])
        current = record or self._saved_record()
        if current is None:
            current = {
                "Id": uuid.uuid4().hex,
                "FriendlyName": f"Android Wireless ({endpoint})",
                "UsbSerial": None,
                "NetworkProfiles": [],
            }
            devices.append(current)
        elif not any(item.get("Id") == current.get("Id") for item in devices):
            devices.append(current)
        current["LastEndpoint"] = endpoint
        current["Mode"] = "wireless-debugging-tls"
        current["IsPaired"] = True
        if record and record.get("MdnsServiceName"):
            current["MdnsServiceName"] = record["MdnsServiceName"]
        current["LastConnectedUtc"] = datetime.now(timezone.utc).isoformat()
        current["LastIp"] = endpoint.rsplit(":", 1)[0].strip("[]") if ":" in endpoint else endpoint
        history = current.setdefault("Endpoints", [])
        history[:] = [item for item in history if (item.get("Endpoint") if isinstance(item, dict) else item) != endpoint]
        history.insert(0, {
            "Endpoint": endpoint,
            "ServiceName": current.get("MdnsServiceName"),
            "LastUsedUtc": current["LastConnectedUtc"],
            "Source": "successful",
        })
        current["Endpoints"] = history[:10]
        store["CurrentDeviceId"] = current["Id"]
        save_devices_store(store, DEVICES_JSON)

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
            record = {
                "Id": uuid.uuid4().hex,
                "FriendlyName": friendly_name.strip() or f"Android Wireless ({selected['host']})",
                "UsbSerial": usb_serial if usb_serial and not is_network_serial(usb_serial) else None,
                "MdnsServiceName": selected["service_name"],
                "NetworkProfiles": [],
            }
            msg = self._connect_modern_endpoint(selected["endpoint"], record=record)
            return True, msg
        except Exception as exc:
            logger.warning("No se pudo configurar Wireless Debugging: %s", exc)
            return False, f"No se pudo configurar Wireless Debugging: {exc}"

    def reconnect(self) -> str:
        """Intenta reconectar el dispositivo actual vía TCP/IP."""
        if not self.serial:
            return "No hay ningún dispositivo seleccionado."
        saved = self._saved_record()
        if saved and saved.get("Mode") == "wireless-debugging-tls":
            return self.discover_and_reconnect_wireless().message
        if not is_network_serial(self.serial):
            return f"El dispositivo {self.serial} es USB. Si no responde, reconecta el cable."
        try:
            msg = get_adb_client().connect(self.serial, timeout=10.0).strip()
            logger.info("Reconexión a %s -> %s", self.serial, msg)
            return msg
        except Exception as exc:
            return f"Error al reconectar: {exc}"

    def discover_and_reconnect_wireless(self, device_id: Optional[str] = None, endpoint: Optional[str] = None) -> ConnectionResult:
        # Busca el endpoint actual, prueba historicos y conecta Wireless Debugging.
        try:
            record = self._saved_record()
            if device_id:
                record = next((item for item in (read_devices_store(DEVICES_JSON).get("Devices") or []) if item.get("Id") == device_id), record)
            try:
                services = self.find_wireless_devices()
            except Exception as mdns_exc:
                logger.debug("mDNS no disponible durante reconexión: %s", mdns_exc)
                services = []
            candidates: List[Dict[str, str]] = list(services)
            for known in self.list_known_endpoints(device_id):
                value = str(known.get("Endpoint") or "")
                if value and not any(item.get("endpoint") == value for item in candidates):
                    candidates.append({"endpoint": value, "host": value.rsplit(":", 1)[0].strip("[]"), "port": value.rsplit(":", 1)[-1], "service_name": str(known.get("ServiceName") or ""), "raw": "historico"})
            if endpoint and not any(item.get("endpoint") == endpoint for item in candidates):
                manual_host = endpoint.rsplit(":", 1)[0].strip("[]")
                candidates.insert(0, {"endpoint": endpoint, "host": manual_host, "port": endpoint.rsplit(":", 1)[-1], "service_name": "", "raw": "manual"})
            if not candidates:
                return ConnectionResult(False, self.serial, method="mdns", message="No se encontraron servicios Wireless Debugging ni endpoints historicos accesibles.")
            preferred_host = self.serial.rsplit(":", 1)[0].strip("[]").lower() if self.serial and ":" in self.serial else ""
            preferred_service = str((record or {}).get("MdnsServiceName") or "").lower()
            matching = [item for item in candidates if endpoint and item.get("endpoint") == endpoint]
            if not matching:
                matching = [item for item in services if preferred_service and item.get("service_name", "").lower() == preferred_service]
            if not matching and len(services) == 1:
                matching = [services[0]]
            if not matching and not services:
                matching = [item for item in candidates if preferred_service and item.get("service_name", "").lower() == preferred_service]
            if not matching:
                matching = [item for item in services if preferred_host and item.get("host", "").lower() == preferred_host]
            if not matching and not services:
                matching = [item for item in candidates if preferred_host and item.get("host", "").lower() == preferred_host]
            selected = matching[0] if len(matching) == 1 else candidates[0] if len(candidates) == 1 else None
            if selected is None:
                return ConnectionResult(False, self.serial, method="mdns", message="Se encontraron varios telefonos Wireless Debugging; selecciona uno.", candidates=candidates)
            try:
                message = self._connect_modern_endpoint(selected["endpoint"], record=record)
                self.save_successful_endpoint((record or {}).get("Id"), selected["endpoint"], selected.get("service_name", ""))
                return ConnectionResult(True, selected["endpoint"], selected["endpoint"], "mdns" if selected in services else "history", message, candidates)
            except Exception as first_exc:
                for alternate in candidates:
                    if alternate.get("endpoint") == selected.get("endpoint"):
                        continue
                    try:
                        message = self._connect_modern_endpoint(alternate["endpoint"], record=record)
                        self.save_successful_endpoint((record or {}).get("Id"), alternate["endpoint"], alternate.get("service_name", ""))
                        return ConnectionResult(True, alternate["endpoint"], alternate["endpoint"], "history", message, candidates)
                    except Exception:
                        continue
                return ConnectionResult(False, self.serial, selected.get("endpoint"), "network", f"No se pudo conectar a los endpoints conocidos: {first_exc}", candidates)
        except Exception as exc:
            return ConnectionResult(False, self.serial, method="diagnostic", message=f"No se pudo buscar Wireless Debugging: {exc}")

    @staticmethod
    def _run_network_command(command: List[str], timeout: float = 8.0) -> str:
        kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout, "check": False}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run(command, **kwargs)
        except Exception as exc:
            return f"ERROR: {exc}"
        return "\\n".join(part for part in (result.stdout or "", result.stderr or "") if part).strip()

    def run_network_diagnostics(self, device_id: Optional[str] = None) -> NetworkDiagnosticReport:
        """Recopila conectividad local, ADB, mDNS, VPN y pruebas TCP accionables."""
        adb_path = get_adb_executable()
        adb_version = self._run_network_command([adb_path, "version"])
        adb_server_status = self._run_network_command([adb_path, "server-status"])
        try:
            adb_devices = self._run_network_command([adb_path, "devices"])
            rows = [line.split() for line in adb_devices.splitlines() if line.strip() and not line.lower().startswith("list of")]
            current_state = next((row[1] for row in rows if row and row[0] == self.serial), None)
            if current_state is None and self.serial is None and len(rows) == 1:
                current_state = rows[0][1]
            current_state = current_state or "ausente"
        except Exception:
            adb_devices = ""
            current_state = "desconocido"

        ipconfig = self._run_network_command(["ipconfig", "/all"])
        route = self._run_network_command(["route", "print"])
        interfaces: List[Dict[str, str]] = []
        current_adapter = ""
        for line in ipconfig.splitlines():
            stripped = line.strip()
            if stripped and not line.startswith(" ") and not line.startswith("	") and line.endswith(":"):
                current_adapter = line.rstrip(":").strip()
            ipv4 = re.search(r"IPv4[^:]*:\s*([0-9a-fA-F:.]+)", line)
            if ipv4 and not ipv4.group(1).startswith("127."):
                interfaces.append({"name": current_adapter or "adaptador", "ip": ipv4.group(1), "status": "active"})
        gateways = sorted(set(re.findall(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+([0-9.]+)", route, re.MULTILINE)))
        active_networks = [f"{item['name']}: {item['ip']}" for item in interfaces]
        vpn_interfaces = [item["name"] for item in interfaces if any(token in item["name"].lower() for token in ("tailscale", "wireguard", "vpn", "wintun"))]
        mdns_error = ""
        try:
            services = self.find_wireless_devices()
        except Exception as exc:
            services = []
            mdns_error = str(exc)

        known = self.list_known_endpoints(device_id)
        endpoints: List[str] = []
        if self.serial and is_network_serial(self.serial):
            endpoints.append(self.serial)
        endpoints.extend(str(item.get("Endpoint")) for item in known if item.get("Endpoint"))
        endpoints.extend(str(item.get("endpoint")) for item in services if item.get("endpoint"))
        unique_endpoints = list(dict.fromkeys(endpoints))
        tcp_tests: List[Dict[str, Any]] = []
        for value in unique_endpoints:
            host, port_text = value.rsplit(":", 1) if ":" in value else (value, "")
            host = host.strip("[]")
            try:
                port = int(port_text)
                with socket.create_connection((host, port), timeout=2.0):
                    tcp_tests.append({"endpoint": value, "reachable": True, "error": None})
            except Exception as exc:
                tcp_tests.append({"endpoint": value, "reachable": False, "error": str(exc)})

        recommendations: List[str] = []
        if current_state == "unauthorized":
            recommendations.append("Desbloquea el telefono y acepta la autorizacion ADB.")
        if not services and unique_endpoints and not any(item["reachable"] for item in tcp_tests):
            errors = " ".join(str(item.get("error") or "") for item in tcp_tests).lower()
            if any(token in errors for token in ("refused", "10061")):
                recommendations.append("El endpoint rechazo la conexion; revisa que Wireless Debugging siga activo y que el puerto sea el actual.")
            elif any(token in errors for token in ("no route", "unreachable", "10051", "10065")):
                recommendations.append("No existe una ruta hacia el telefono; conecta ambos equipos a la misma red o usa una VPN con rutas permitidas.")
            elif any(token in errors for token in ("timed out", "timeout", "10060")):
                recommendations.append("La conexion agoto el tiempo de espera; revisa aislamiento de clientes WiFi, firewall y VPN.")
            recommendations.append("mDNS no respondio y ningun endpoint historico es accesible; verifica que exista una ruta de red.")
        if not interfaces:
            recommendations.append("No se detecto una interfaz IPv4 activa en la PC.")
        if vpn_interfaces:
            recommendations.append("Hay una interfaz VPN activa; mDNS puede no cruzarla, usa el endpoint historico o DNS de la VPN.")
        if mdns_error:
            recommendations.append("mDNS no esta disponible en ADB; se utilizaran endpoints historicos o una direccion DNS de la VPN.")
        if not recommendations and current_state != "device":
            recommendations.append("El dispositivo no esta en estado device; revisa WiFi, autorizacion y firewall.")
        if not recommendations:
            recommendations.append("La conectividad ADB y la red parecen disponibles.")

        return NetworkDiagnosticReport(
            adb_path=adb_path,
            adb_version=adb_version,
            adb_server_status=adb_server_status,
            adb_state=current_state,
            current_serial=self.serial,
            current_endpoint=self.serial if is_network_serial(self.serial or "") else None,
            known_endpoints=known,
            interfaces=interfaces,
            gateways=gateways,
            active_networks=active_networks,
            mdns_services=services,
            tcp_tests=tcp_tests,
            vpn_interfaces=vpn_interfaces,
            recommendations=recommendations,
        )

    def start_connection_watchdog(
        self,
        device_id: Optional[str] = None,
        retry_policy: Optional[RetryPolicy | Dict[str, float]] = None,
        on_result: Optional[Callable[[ConnectionResult], None]] = None,
    ) -> WatchHandle:
        """Supervisa el dispositivo activo y limita los reintentos de red."""
        self.stop_connection_watchdog()
        if isinstance(retry_policy, dict):
            policy = RetryPolicy(**retry_policy)
        else:
            policy = retry_policy or RetryPolicy()
        stop_event = threading.Event()
        self._watchdog_stop = stop_event
        self._watchdog_device_id = device_id
        self._watchdog_policy = policy

        def loop() -> None:
            interval = max(0.5, policy.initial_interval)
            deadline: Optional[float] = None
            while not stop_event.wait(interval):
                try:
                    devices = get_adb_client().list()
                    info = next((item for item in devices if item.serial == self.serial), None)
                    if info is not None and info.state == "device":
                        deadline = None
                        interval = max(0.5, policy.initial_interval)
                        continue
                    usb_fallback = next((item for item in devices if item.state == "device" and not is_network_serial(item.serial)), None)
                    if usb_fallback is not None:
                        self.serial = usb_fallback.serial
                        deadline = None
                        interval = max(0.5, policy.initial_interval)
                        if on_result:
                            on_result(ConnectionResult(True, usb_fallback.serial, usb_fallback.serial, "usb", "Conexión USB disponible; se usa como respaldo."))
                        continue
                    if deadline is None:
                        deadline = time.monotonic() + max(1.0, policy.timeout)
                    if time.monotonic() >= deadline:
                        logger.warning("Supervisor de reconexion detenido tras alcanzar el limite de %.0fs.", policy.timeout)
                        break
                    result = self.discover_and_reconnect_wireless(device_id=device_id)
                    if on_result:
                        on_result(result)
                    if result.success:
                        deadline = None
                        interval = max(0.5, policy.initial_interval)
                    else:
                        interval = min(max(interval * 2, policy.initial_interval), policy.max_interval)
                except Exception as exc:
                    logger.debug("Error en supervisor de reconexion: %s", exc)
                    interval = min(max(interval * 2, policy.initial_interval), policy.max_interval)
            stop_event.set()

        thread = threading.Thread(target=loop, name="device-network-watchdog", daemon=True)
        self._watchdog_thread = thread
        thread.start()
        return WatchHandle(stop_event, thread)

    def stop_connection_watchdog(self) -> None:
        """Detiene el supervisor actual y evita que sobreviva al cambio de dispositivo."""
        self._watchdog_stop.set()
        thread = self._watchdog_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._watchdog_thread = None
        self._watchdog_device_id = None

    def convert_usb_to_wifi(self, usb_serial: Optional[str] = None) -> Tuple[bool, str]:
        """
        Convierte una conexión USB activa a Wi-Fi de forma robusta.
        
        FLUJO CRÍTICO:
        1. Obtiene el modelo y la dirección IP de wlan0 MIENTRAS la conexión USB está activa y estable.
        2. Activa `tcpip 5555`.
        3. Espera a que el daemon `adbd` del teléfono termine de reiniciar en modo TCP.
        4. Conecta mediante `adb connect <IP>:5555`.
        5. Guarda el dispositivo en `devices.json` y conmuta el serial activo a la conexión inalámbrica.
        """
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

            # 1. PASO CRÍTICO: Obtener modelo e IP de Wi-Fi ANTES de reiniciar adbd en tcpip
            logger.info("Obteniendo dirección IP Wi-Fi de %s sobre canal USB activo...", serial)
            model = dev.getprop("ro.product.model") or "Android Phone"

            ip = None
            try:
                ip = dev.wlan_ip()
            except Exception:
                pass

            if not ip or ip == "127.0.0.1":
                # Fallback con ip -4 addr show wlan0
                out = dev.shell("ip -4 addr show wlan0") or ""
                m = re.search(r"inet\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", out)
                ip = m.group(1) if m else None

            if not ip or ip == "127.0.0.1":
                return False, "No se pudo obtener la IP Wi-Fi de 'wlan0'. Asegúrate de que el celular esté conectado a la misma red Wi-Fi."

            endpoint = f"{ip}:5555"
            logger.info("IP Wi-Fi detectada: %s. Activando TCP/IP en puerto 5555...", ip)

            # 2. Habilitar modo TCP/IP en puerto 5555
            try:
                dev.tcpip(5555)
            except Exception as tcp_exc:
                # Al cambiar a modo TCP/IP, adbd puede cerrar el socket USB de inmediato
                logger.debug("Respuesta en comando tcpip (esperable): %s", tcp_exc)

            # 3. Esperar 2 segundos a que el daemon del teléfono arranque y escuche en el socket TCP
            time.sleep(2.0)

            # 4. Conectar vía ADB TCP/IP
            logger.info("Conectando inalámbricamente a %s...", endpoint)
            conn_res = client.connect(endpoint, timeout=12.0)
            logger.info("Resultado de conexión Wi-Fi: %s", conn_res.strip())

            if "connected to" not in conn_res.lower() and "already connected" not in conn_res.lower():
                # Reintento adicional por latencia de red
                time.sleep(1.0)
                conn_res = client.connect(endpoint, timeout=12.0)
                logger.info("Segundo intento de conexión Wi-Fi: %s", conn_res.strip())

            # 5. Guardar en devices.json
            self._save_wifi_device_to_store(model=model, usb_serial=serial, endpoint=endpoint)

            # 6. Cambiar serial activo al nuevo endpoint inalámbrico
            self.serial = endpoint
            self._auto_converted_serials.add(serial)

            return True, f"¡Conexión Wi-Fi establecida con éxito a {endpoint}! Ya puedes desconectar el cable USB."

        except Exception as exc:
            logger.error("Error al convertir USB a Wi-Fi: %s", exc)
            return False, f"Fallo al convertir a Wi-Fi: {exc}"

    def _save_wifi_device_to_store(self, model: str, usb_serial: str, endpoint: str) -> None:
        """Persiste el nuevo dispositivo en devices.json para compatibilidad total."""
        store = read_devices_store(DEVICES_JSON)
        devices = store.setdefault("Devices", [])

        # Buscar si ya existía
        existing = next(
            (d for d in devices if d.get("UsbSerial") == usb_serial or d.get("LastEndpoint") == endpoint),
            None,
        )

        now_utc = datetime.now(timezone.utc).isoformat()
        if existing:
            existing["LastEndpoint"] = endpoint
            existing["LastIp"] = endpoint.rsplit(":", 1)[0].strip("[]")
            existing["LastConnectedUtc"] = now_utc
            existing["Mode"] = "tcpip-5555"
            history = existing.setdefault("Endpoints", [])
            history[:] = [item for item in history if (item.get("Endpoint") if isinstance(item, dict) else item) != endpoint]
            history.insert(0, {"Endpoint": endpoint, "LastUsedUtc": now_utc, "Source": "successful"})
            existing["Endpoints"] = history[:10]
            device_id = existing["Id"]
        else:
            device_id = uuid.uuid4().hex
            devices.append(
                {
                    "Id": device_id,
                    "FriendlyName": f"{model} ({endpoint})",
                    "UsbSerial": usb_serial,
                    "LastEndpoint": endpoint,
                    "LastIp": endpoint.rsplit(":", 1)[0].strip("[]"),
                    "Mode": "tcpip-5555",
                    "LastConnectedUtc": now_utc,
                    "IsPaired": True,
                    "NetworkProfiles": [
                        {
                            "Id": uuid.uuid4().hex,
                            "Name": "Wi-Fi Principal",
                            "PreferredEndpoint": endpoint,
                            "ForceUsbOnly": False,
                        }
                    ],
                    "Endpoints": [{"Endpoint": endpoint, "LastUsedUtc": now_utc, "Source": "successful"}],
                }
            )

        store["CurrentDeviceId"] = device_id
        save_devices_store(store, DEVICES_JSON)
