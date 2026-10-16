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
        logger.info("Serial inicial configurado: %s", self.serial or "autodetección")

    def init_adb(self) -> None:
        """Arranca o verifica el daemon ADB."""
        ensure_single_adb_server(force=False)

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
            services.append({
                "service_name": line.strip().split()[0] if line.strip() else "",
                "host": host,
                "port": match.group("port"),
                "endpoint": f"[{host}]:{match.group('port')}" if ":" in host else f"{host}:{match.group('port')}",
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
            return self.discover_and_reconnect_wireless()
        if not is_network_serial(self.serial):
            return f"El dispositivo {self.serial} es USB. Si no responde, reconecta el cable."
        try:
            msg = get_adb_client().connect(self.serial, timeout=10.0).strip()
            logger.info("Reconexión a %s -> %s", self.serial, msg)
            return msg
        except Exception as exc:
            return f"Error al reconectar: {exc}"

    def discover_and_reconnect_wireless(self) -> str:
        """Busca el nuevo endpoint mDNS tras un cambio de red y lo conecta."""
        try:
            record = self._saved_record()
            services = self.find_wireless_devices()
            if not services:
                return "No se encontraron servicios Wireless Debugging por mDNS en esta red."
            preferred_host = ""
            if self.serial and ":" in self.serial:
                preferred_host = self.serial.rsplit(":", 1)[0].strip("[]").lower()
            preferred_service = str((record or {}).get("MdnsServiceName") or "").lower()
            matching = [item for item in services if preferred_service and item["service_name"].lower() == preferred_service]
            if not matching:
                matching = [item for item in services if preferred_host and item["host"].lower() == preferred_host]
            selected = matching[0] if len(matching) == 1 else services[0] if len(services) == 1 else None
            if selected is None:
                return "Se encontraron varios teléfonos Wireless Debugging; selecciona uno desde Buscar dispositivo WiFi."
            return self._connect_modern_endpoint(selected["endpoint"], record=record)
        except Exception as exc:
            return f"No se pudo buscar Wireless Debugging: {exc}"

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
            existing["LastConnectedUtc"] = now_utc
            existing["Mode"] = "tcpip-5555"
            device_id = existing["Id"]
        else:
            device_id = uuid.uuid4().hex
            devices.append(
                {
                    "Id": device_id,
                    "FriendlyName": f"{model} ({endpoint})",
                    "UsbSerial": usb_serial,
                    "LastEndpoint": endpoint,
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
                }
            )

        store["CurrentDeviceId"] = device_id
        save_devices_store(store, DEVICES_JSON)
