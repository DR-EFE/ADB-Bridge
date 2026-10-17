"""QR pairing for Android 11+ Wireless Debugging.

The phone scans a QR rendered by the desktop.  Android then publishes a
temporary ``_adb-tls-pairing`` mDNS service; the normal ``adb pair`` command is
used with the password from the QR and the resulting TLS connect service is
connected automatically.
"""

from __future__ import annotations

import base64
import io
import secrets
import string
import subprocess
import time
from dataclasses import dataclass
from threading import Event
from typing import Callable, Optional

from ui_automation.adb_manager import get_adb_executable

try:
    import qrcode
except ImportError:  # pragma: no cover - gives a useful UI error at runtime
    qrcode = None


PAIR_SERVICE = "_adb-tls-pairing"
CONNECT_SERVICE = "_adb-tls-connect"
DEFAULT_TIMEOUT = 120.0


class WirelessQrError(RuntimeError):
    """Error controlado durante la sesión de emparejamiento QR."""


@dataclass(frozen=True)
class WirelessQrPayload:
    service_name: str
    password: str
    value: str
    image_base64: str


@dataclass(frozen=True)
class WirelessQrResult:
    success: bool
    endpoint: Optional[str] = None
    service_name: Optional[str] = None
    message: str = ""


ProgressCallback = Callable[[str], None]


class WirelessQrPairingService:
    """Genera el QR y coordina descubrimiento, pairing y conexión mediante ADB."""

    def __init__(self, adb_path: Optional[str] = None) -> None:
        self.adb_path = adb_path or get_adb_executable()
        self._cancel = Event()

    def create_payload(self) -> WirelessQrPayload:
        if qrcode is None:
            raise WirelessQrError("Falta la dependencia qrcode. Instálala con: python -m pip install qrcode[pil]")
        service_name = f"androidtools-{secrets.token_hex(4)}"
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(12))
        value = f"WIFI:T:ADB;S:{service_name};P:{password};;"
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(value)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return WirelessQrPayload(service_name, password, value, base64.b64encode(buffer.getvalue()).decode("ascii"))

    def cancel(self) -> None:
        self._cancel.set()

    def pair(
        self,
        payload: WirelessQrPayload,
        timeout: float = DEFAULT_TIMEOUT,
        progress: Optional[ProgressCallback] = None,
    ) -> WirelessQrResult:
        self._cancel.clear()
        self._ensure_mdns()
        self._notify(progress, "QR generado; esperando que el teléfono lo escanee…")
        pairing = self._wait_for_service(payload.service_name, timeout, progress)
        self._notify(progress, "Teléfono detectado; completando emparejamiento…")
        output = self._run("pair", f"{pairing['host']}:{pairing['port']}", payload.password, timeout=25)
        if not _successful_pair(output):
            raise WirelessQrError("El teléfono rechazó el emparejamiento QR o la contraseña expiró.")
        self._notify(progress, "Emparejamiento correcto; esperando conexión segura…")
        endpoint = self._wait_for_connection(pairing["host"], 30, progress)
        if endpoint:
            self._run("connect", endpoint, timeout=15)
        else:
            endpoint = self._connected_endpoint(pairing["host"])
        return WirelessQrResult(True, endpoint, payload.service_name, "Emparejamiento QR completado correctamente.")

    def _connected_endpoint(self, host: str) -> Optional[str]:
        try:
            output = self._run("devices", timeout=10)
        except WirelessQrError:
            return None
        for line in output.splitlines():
            columns = line.split()
            if len(columns) >= 2 and columns[1] == "device" and host in columns[0]:
                return columns[0]
        return None

    def _wait_for_service(self, service_name: str, timeout: float, progress: Optional[ProgressCallback]) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                raise WirelessQrError("Emparejamiento QR cancelado.")
            for row in self._mdns_services():
                if row["service"] == PAIR_SERVICE or row["service"].startswith(PAIR_SERVICE):
                    if row["name"] == service_name:
                        return row
            self._notify(progress, "Esperando escaneo del QR…")
            time.sleep(1)
        raise WirelessQrError("Tiempo agotado: no se detectó el teléfono. Verifica la misma red Wi‑Fi y mDNS.")

    def _wait_for_connection(self, host: str, timeout: float, progress: Optional[ProgressCallback]) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                raise WirelessQrError("Emparejamiento QR cancelado después de emparejar.")
            for row in self._mdns_services():
                if row["service"] == CONNECT_SERVICE or row["service"].startswith(CONNECT_SERVICE):
                    if row["host"] == host:
                        return f"{host}:{row['port']}"
            self._notify(progress, "Esperando endpoint seguro del teléfono…")
            time.sleep(1)
        # ADB moderno puede conectar automáticamente después del pairing.
        return None

    def _mdns_services(self) -> list[dict[str, str]]:
        output = self._run("mdns", "services", timeout=10)
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.rstrip("\r").split("\t") if "\t" in line else line.split()
            if len(parts) < 3 or not parts[1].startswith("_adb-tls-"):
                continue
            host, separator, port = parts[2].rpartition(":")
            if not separator or not port.isdigit():
                continue
            rows.append({"name": parts[0], "service": parts[1], "host": host.strip("[]"), "port": port})
        return rows

    def _ensure_mdns(self) -> None:
        try:
            output = self._run("mdns", "check", timeout=10)
        except WirelessQrError:
            output = ""
        if "daemon version" not in output.lower():
            # ADB 31+ enables the openscreen backend through this environment.
            self._run("kill-server", timeout=15)

    def _run(self, *arguments: str, timeout: float = 15) -> str:
        try:
            result = subprocess.run(
                [self.adb_path, *arguments], capture_output=True, text=True, timeout=timeout,
                env={**__import__("os").environ, "ADB_MDNS_OPENSCREEN": "1"}, check=False,
            )
        except FileNotFoundError as exc:
            raise WirelessQrError("No se encontró adb. Instala Android Platform Tools.") from exc
        except subprocess.TimeoutExpired as exc:
            raise WirelessQrError(f"ADB no respondió a tiempo: {' '.join(arguments)}") from exc
        output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        if result.returncode != 0:
            raise WirelessQrError(output or f"ADB falló: {' '.join(arguments)}")
        return output

    @staticmethod
    def _notify(progress: Optional[ProgressCallback], message: str) -> None:
        if progress:
            progress(message)


def _successful_pair(output: str) -> bool:
    normalized = output.casefold()
    return "successfully paired" in normalized or "pairing successful" in normalized or "emparejamiento exitoso" in normalized
