"""Descubrimiento de red orientado a dispositivos Android mediante Nmap.

Este módulo no ejecuta argumentos arbitrarios: genera comandos a partir de
perfiles conocidos, analiza la salida XML de Nmap y no modifica el inventario
de dispositivos. La conexión y persistencia siguen siendo responsabilidad de
``DeviceService``.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


class ScanProfile(str, Enum):
    QUICK = "quick"
    WIRELESS = "wireless"
    KNOWN = "known"
    DEEP = "deep"
    SECURITY = "security"


@dataclass
class LocalNetwork:
    name: str
    address: str
    cidr: str
    is_default: bool = False


@dataclass
class PortScanResult:
    port: int
    protocol: str = "tcp"
    state: str = "unknown"
    reason: str = ""
    service: str = ""
    product: str = ""
    version: str = ""

    @property
    def label(self) -> str:
        detail = " ".join(part for part in (self.service, self.product, self.version) if part)
        return f"{self.port}/{self.protocol} {self.state}" + (f" ({detail})" if detail else "")


@dataclass
class NetworkHost:
    ip: str
    status: str = "unknown"
    hostname: str = ""
    mac: str = ""
    vendor: str = ""
    latency_ms: Optional[float] = None
    ports: List[PortScanResult] = field(default_factory=list)
    mdns_services: List[str] = field(default_factory=list)
    known_device_id: Optional[str] = None
    known_device_name: Optional[str] = None
    match_type: str = "new"
    auto_connect: bool = False
    risks: List[str] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)

    @property
    def adb_ports(self) -> List[int]:
        return [item.port for item in self.ports if item.protocol == "tcp" and item.state == "open"]

    @property
    def adb_candidate(self) -> bool:
        return bool(self.adb_ports or self.mdns_services)

    @property
    def endpoints(self) -> List[str]:
        return [f"{self.ip}:{port}" for port in self.adb_ports if self.ip]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["ports"] = [asdict(port) for port in self.ports]
        return data


@dataclass
class NetworkScanResult:
    profile: str
    target: str
    started_utc: str
    finished_utc: str = ""
    hosts: List[NetworkHost] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    command: List[str] = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "target": self.target,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "hosts": [host.to_dict() for host in self.hosts],
            "changes": self.changes,
            "warnings": self.warnings,
            "command": self.command,
            "cancelled": self.cancelled,
        }


def normalize_mac(value: str) -> str:
    """Devuelve una MAC comparable o cadena vacía para entradas inválidas."""
    raw = re.sub(r"[^0-9A-Fa-f]", "", value or "").upper()
    return raw if len(raw) == 12 and re.fullmatch(r"[0-9A-F]{12}", raw) else ""


class NetworkScanError(RuntimeError):
    """Error recuperable de la capa de escaneo."""


class NetworkScanService:
    """Ejecuta perfiles de Nmap, coteja inventario y conserva historial corto."""

    HISTORY_LIMIT = 30

    def __init__(self, history_path: Optional[Path] = None, nmap_path: Optional[str] = None) -> None:
        base = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())) / "AndroidTools"
        self.history_path = history_path or base / "network-scan-history.json"
        self._configured_nmap = nmap_path
        self._process_lock = threading.Lock()
        self._process: Optional[subprocess.Popen[str]] = None

    @staticmethod
    def resolve_nmap(configured_path: Optional[str] = None) -> Optional[str]:
        candidates = [configured_path, shutil.which("nmap"), shutil.which("nmap.exe")]
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(env_key)
            if root:
                candidates.append(str(Path(root) / "Nmap" / "nmap.exe"))
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        return None

    @staticmethod
    def find_advanced_ip_scanner() -> Optional[str]:
        candidates: List[Path] = []
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(env_key)
            if root:
                candidates.extend(
                    [
                        Path(root) / "Advanced IP Scanner" / "Advanced IP Scanner.exe",
                        Path(root) / "Advanced IP Scanner" / "advanced_ip_scanner.exe",
                    ]
                )
        return next((str(path) for path in candidates if path.is_file()), None)

    @staticmethod
    def detect_local_networks() -> List[LocalNetwork]:
        """Obtiene redes IPv4 privadas mediante un comando fijo de Windows.

        El fallo no bloquea la app: se entrega una red /24 aproximada de la
        interfaz con ruta de salida como alternativa.
        """
        script = (
            "Get-NetIPConfiguration -ErrorAction SilentlyContinue | "
            "Where-Object {$_.IPv4Address} | ForEach-Object {$c=$_; $c.IPv4Address | "
            "ForEach-Object {[pscustomobject]@{name=$c.InterfaceAlias;ip=$_.IPAddress;prefix=$_.PrefixLength;"
            "default=([bool]$c.IPv4DefaultGateway)}}} | ConvertTo-Json -Compress"
        )
        found: List[LocalNetwork] = []
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            payload = json.loads(result.stdout) if result.stdout.strip() else []
            if isinstance(payload, dict):
                payload = [payload]
            for item in payload:
                try:
                    address = ipaddress.ip_address(str(item.get("ip") or ""))
                    prefix = int(item.get("prefix"))
                    if not address.is_private or address.is_link_local or not 1 <= prefix <= 30:
                        continue
                    network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
                except (ValueError, TypeError):
                    continue
                candidate = LocalNetwork(
                    str(item.get("name") or "Red"), str(address), str(network), bool(item.get("default"))
                )
                if candidate.cidr not in {entry.cidr for entry in found}:
                    found.append(candidate)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        preferred_names = ("wi-fi", "wifi", "wlan", "ethernet")
        found.sort(key=lambda item: (
            0 if item.is_default else 1,
            0 if any(name in item.name.lower() for name in preferred_names) else 1,
            item.name.lower(),
        ))
        return found

    @staticmethod
    def _endpoint_parts(endpoint: str) -> tuple[str, Optional[int]]:
        value = (endpoint or "").strip().strip("[]")
        if value.count(":") != 1:
            return value, None
        host, raw_port = value.rsplit(":", 1)
        try:
            port = int(raw_port)
        except ValueError:
            return host, None
        return host, port if 1 <= port <= 65535 else None

    @classmethod
    def learned_ports(cls, saved_devices: Sequence[Dict[str, Any]]) -> List[int]:
        ports = {5555}
        for device in saved_devices:
            candidates = [device.get("LastEndpoint")]
            candidates.extend(
                entry.get("Endpoint") if isinstance(entry, dict) else entry
                for entry in device.get("Endpoints") or []
            )
            for endpoint in candidates:
                _, port = cls._endpoint_parts(str(endpoint or ""))
                if port:
                    ports.add(port)
        return sorted(ports)

    @classmethod
    def known_targets(cls, saved_devices: Sequence[Dict[str, Any]]) -> List[str]:
        targets = set()
        for device in saved_devices:
            if device.get("LastIp"):
                targets.add(str(device["LastIp"]).strip())
            candidates = [device.get("LastEndpoint")]
            candidates.extend(
                entry.get("Endpoint") if isinstance(entry, dict) else entry
                for entry in device.get("Endpoints") or []
            )
            for endpoint in candidates:
                host, _ = cls._endpoint_parts(str(endpoint or ""))
                try:
                    ipaddress.ip_address(host)
                    targets.add(host)
                except ValueError:
                    continue
        return sorted(targets)

    @staticmethod
    def validate_target(target: str) -> str:
        value = (target or "").strip()
        try:
            return str(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise NetworkScanError("El destino debe ser una red IPv4 CIDR, por ejemplo 192.168.1.0/24.") from exc

    def build_command(
        self,
        profile: ScanProfile | str,
        target: str,
        saved_devices: Sequence[Dict[str, Any]],
    ) -> List[str]:
        try:
            selected = ScanProfile(profile)
        except ValueError as exc:
            raise NetworkScanError("Perfil de escaneo no reconocido.") from exc
        nmap = self.resolve_nmap(self._configured_nmap)
        if not nmap:
            raise NetworkScanError("Nmap no está instalado o no se encontró nmap.exe.")

        learned = self.learned_ports(saved_devices)
        target_args = [self.validate_target(target)]
        if selected is ScanProfile.KNOWN:
            known = self.known_targets(saved_devices)
            if not known:
                raise NetworkScanError("No hay IPs ni endpoints guardados para el perfil Solo conocidos.")
            target_args = known

        ports: List[str] = [str(port) for port in learned]
        if selected in (ScanProfile.WIRELESS, ScanProfile.SECURITY):
            ports.append("37000-45000")
        port_spec = ",".join(dict.fromkeys(ports))
        command = [nmap, "-n", "-T4", "--host-timeout", "12s", "-p", port_spec, "-oX", "-"]
        if selected is ScanProfile.KNOWN:
            command.append("-Pn")
        if selected is ScanProfile.DEEP:
            command.extend(["-sV", "-O", "--osscan-limit"])
        if selected is ScanProfile.SECURITY:
            command.append("--reason")
        command.extend(target_args)
        return command

    def cancel_active_scan(self) -> None:
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()

    def scan(
        self,
        profile: ScanProfile | str,
        target: str,
        saved_devices: Sequence[Dict[str, Any]],
        mdns_services: Optional[Sequence[Dict[str, str]]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> NetworkScanResult:
        selected = ScanProfile(profile)
        command = self.build_command(selected, target, saved_devices)
        result = NetworkScanResult(
            profile=selected.value,
            target=target,
            started_utc=datetime.now(timezone.utc).isoformat(),
            command=command,
        )
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        }
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise NetworkScanError(f"No se pudo iniciar Nmap: {exc}") from exc
        with self._process_lock:
            self._process = process
        try:
            while process.poll() is None:
                if cancel_event and cancel_event.is_set():
                    result.cancelled = True
                    process.terminate()
                    break
                time.sleep(0.1)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                result.cancelled = True
        finally:
            with self._process_lock:
                self._process = None

        if result.cancelled:
            result.warnings.append("Escaneo cancelado.")
            result.finished_utc = datetime.now(timezone.utc).isoformat()
            return result
        if not stdout.strip():
            raise NetworkScanError((stderr or "Nmap no devolvió resultados.").strip())
        result.hosts = self.parse_nmap_xml(stdout)
        self.merge_mdns(result.hosts, mdns_services or [])
        self.match_inventory(result.hosts, saved_devices)
        self.classify_hosts(result.hosts)
        result.changes = self.compare_and_save(result)
        result.finished_utc = datetime.now(timezone.utc).isoformat()
        if process.returncode not in (0, None):
            result.warnings.append((stderr or f"Nmap finalizó con código {process.returncode}.").strip())
        return result

    @staticmethod
    def parse_nmap_xml(xml_text: str) -> List[NetworkHost]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise NetworkScanError(f"Nmap devolvió XML inválido: {exc}") from exc
        hosts: List[NetworkHost] = []
        for node in root.findall("host"):
            state = (node.find("status").get("state") if node.find("status") is not None else "unknown")
            addresses = node.findall("address")
            ip = next((entry.get("addr", "") for entry in addresses if entry.get("addrtype") in ("ipv4", "ipv6")), "")
            if not ip:
                continue
            mac_node = next((entry for entry in addresses if entry.get("addrtype") == "mac"), None)
            hostnames = node.findall("hostnames/hostname")
            hostname = hostnames[0].get("name", "") if hostnames else ""
            latency: Optional[float] = None
            times = node.find("times")
            if times is not None and times.get("srtt"):
                try:
                    latency = round(int(times.get("srtt", "0")) / 1000, 1)
                except ValueError:
                    pass
            host = NetworkHost(
                ip=ip,
                status=state,
                hostname=hostname,
                mac=normalize_mac(mac_node.get("addr", "") if mac_node is not None else ""),
                vendor=mac_node.get("vendor", "") if mac_node is not None else "",
                latency_ms=latency,
            )
            for port in node.findall("ports/port"):
                state_node = port.find("state")
                service = port.find("service")
                try:
                    number = int(port.get("portid", "0"))
                except ValueError:
                    continue
                host.ports.append(
                    PortScanResult(
                        port=number,
                        protocol=port.get("protocol", "tcp"),
                        state=state_node.get("state", "unknown") if state_node is not None else "unknown",
                        reason=state_node.get("reason", "") if state_node is not None else "",
                        service=service.get("name", "") if service is not None else "",
                        product=service.get("product", "") if service is not None else "",
                        version=service.get("version", "") if service is not None else "",
                    )
                )
            hosts.append(host)
        return hosts

    @classmethod
    def merge_mdns(cls, hosts: List[NetworkHost], services: Sequence[Dict[str, str]]) -> None:
        by_ip = {host.ip: host for host in hosts}
        for service in services:
            host_name = str(service.get("host") or "").strip("[]")
            try:
                ipaddress.ip_address(host_name)
            except ValueError:
                continue
            host = by_ip.get(host_name)
            if not host:
                host = NetworkHost(ip=host_name, status="mdns")
                hosts.append(host)
                by_ip[host_name] = host
            service_name = str(service.get("service_name") or service.get("raw") or "mDNS")
            if service_name not in host.mdns_services:
                host.mdns_services.append(service_name)
            try:
                port = int(service.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            if port and not any(item.port == port for item in host.ports):
                host.ports.append(PortScanResult(port=port, state="open", service="adb-tls-connect"))

    @classmethod
    def match_inventory(cls, hosts: Iterable[NetworkHost], saved_devices: Sequence[Dict[str, Any]]) -> None:
        mac_matches = {
            normalize_mac(str(item.get("WifiMacAddress") or "")): item
            for item in saved_devices
            if normalize_mac(str(item.get("WifiMacAddress") or ""))
        }
        by_ip: Dict[str, Dict[str, Any]] = {}
        mdns_matches = {
            str(item.get("MdnsServiceName") or "").strip().lower(): item
            for item in saved_devices
            if str(item.get("MdnsServiceName") or "").strip()
        }
        for item in saved_devices:
            for value in (item.get("LastIp"), cls._endpoint_parts(str(item.get("LastEndpoint") or ""))[0]):
                if value:
                    by_ip[str(value)] = item
        for host in hosts:
            strong = mac_matches.get(host.mac)
            if strong:
                host.known_device_id = str(strong.get("Id") or "") or None
                host.known_device_name = str(strong.get("FriendlyName") or "Android")
                host.match_type = "mac"
                host.auto_connect = bool(host.known_device_id and host.adb_candidate)
                continue
            mdns = next((mdns_matches.get(value.strip().lower()) for value in host.mdns_services if mdns_matches.get(value.strip().lower())), None)
            if mdns:
                host.known_device_id = str(mdns.get("Id") or "") or None
                host.known_device_name = str(mdns.get("FriendlyName") or "Android")
                host.match_type = "mdns"
                continue
            possible = by_ip.get(host.ip)
            if possible:
                host.known_device_id = str(possible.get("Id") or "") or None
                host.known_device_name = str(possible.get("FriendlyName") or "Android")
                host.match_type = "ip"

    @staticmethod
    def classify_hosts(hosts: Iterable[NetworkHost]) -> None:
        for host in hosts:
            if host.adb_candidate and not host.known_device_id:
                host.risks.append("ADB abierto en dispositivo no registrado.")
            if host.mdns_services and not host.adb_ports:
                host.risks.append("Posible aislamiento AP: mDNS visible, pero el puerto no respondió al escaneo.")
            if any(port.state == "filtered" for port in host.ports):
                host.risks.append("Hay puertos filtrados por firewall o por la red.")
            if host.status == "up" and not host.adb_candidate:
                host.risks.append("Host activo sin servicio ADB detectable.")

    def _read_history(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            scans = data.get("scans", []) if isinstance(data, dict) else []
            return scans if isinstance(scans, list) else []
        except (OSError, ValueError):
            return []

    def compare_and_save(self, result: NetworkScanResult) -> List[str]:
        history = self._read_history()
        # Solo comparar ejecuciones equivalentes: cambiar de perfil cambia los
        # puertos consultados y un falso "desapareció" sería engañoso.
        previous = next(
            (
                scan for scan in reversed(history)
                if isinstance(scan, dict)
                and scan.get("profile") == result.profile
                and scan.get("target") == result.target
            ),
            {},
        )
        old_hosts = previous.get("hosts", []) if isinstance(previous, dict) else []
        old_by_key = {
            (normalize_mac(str(item.get("mac") or "")) or str(item.get("ip") or "")): item
            for item in old_hosts if isinstance(item, dict)
        }
        current_keys = set()
        changes: List[str] = []
        for host in result.hosts:
            key = host.mac or host.ip
            current_keys.add(key)
            old = old_by_key.get(key)
            label = host.known_device_name or host.hostname or host.ip
            if not old:
                host.changes.append("Nuevo en el escaneo")
                changes.append(f"Nuevo: {label}")
                continue
            if old.get("ip") != host.ip:
                host.changes.append(f"IP cambió: {old.get('ip')} → {host.ip}")
                changes.append(f"IP cambió: {label}")
            if old.get("hostname", "") != host.hostname:
                host.changes.append("Hostname cambió")
                changes.append(f"Hostname cambió: {label}")
            old_ports = {int(port.get("port")) for port in old.get("ports", []) if isinstance(port, dict) and str(port.get("state")) == "open"}
            new_ports = set(host.adb_ports)
            if old_ports != new_ports:
                host.changes.append("Puertos ADB cambiaron")
                changes.append(f"Puertos cambiaron: {label}")
        for key, old in old_by_key.items():
            if key not in current_keys:
                label = old.get("known_device_name") or old.get("hostname") or old.get("ip") or "host"
                changes.append(f"Desapareció: {label}")

        snapshot = result.to_dict()
        snapshot["finished_utc"] = datetime.now(timezone.utc).isoformat()
        history.append(snapshot)
        history = history[-self.HISTORY_LIMIT :]
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=self.history_path.parent, suffix=".tmp") as handle:
                json.dump({"scans": history}, handle, ensure_ascii=False, indent=2)
                temporary = Path(handle.name)
            os.replace(temporary, self.history_path)
        except OSError as exc:
            result.warnings.append(f"No se pudo guardar el historial: {exc}")
        return changes

    @staticmethod
    def export(result: NetworkScanResult, output_path: Path, fmt: str) -> Path:
        fmt = fmt.lower()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            output_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return output_path
        if fmt != "csv":
            raise NetworkScanError("Formato de exportación no admitido.")
        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["ip", "hostname", "mac", "vendor", "status", "latency_ms", "ports", "known_device", "match", "risks", "changes"])
            writer.writeheader()
            for host in result.hosts:
                writer.writerow({
                    "ip": host.ip,
                    "hostname": host.hostname,
                    "mac": host.mac,
                    "vendor": host.vendor,
                    "status": host.status,
                    "latency_ms": host.latency_ms if host.latency_ms is not None else "",
                    "ports": "; ".join(port.label for port in host.ports),
                    "known_device": host.known_device_name or "",
                    "match": host.match_type,
                    "risks": " | ".join(host.risks),
                    "changes": " | ".join(host.changes),
                })
        return output_path
