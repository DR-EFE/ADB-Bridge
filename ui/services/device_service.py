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

    def reconnect(self) -> str:
        """Intenta reconectar el dispositivo actual vía TCP/IP."""
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
