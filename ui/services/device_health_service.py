"""Diagnóstico de salud Android mediante el contrato existente de AdbService."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .database import SQLiteStore
from .device_health_models import DeviceHealthSnapshot, DeviceHealthTrends, HealthStatus, HealthTrend


class DeviceHealthService:
    """Recopila datos best-effort, preservando ausencia y errores explícitamente."""

    def __init__(self, adb_service: Any, store: Optional[SQLiteStore] = None) -> None:
        self._adb = adb_service
        self._store = store or SQLiteStore()

    @staticmethod
    def _output(result: Any) -> str:
        return str(getattr(result, "output", "") or "").strip()

    @staticmethod
    def _parse_battery(text: str) -> tuple[Optional[int], Optional[str], Optional[float]]:
        level = re.search(r"(?m)^\s*level:\s*(\d+)", text)
        status = re.search(r"(?m)^\s*status:\s*(\d+)", text)
        temp = re.search(r"(?m)^\s*temperature:\s*(-?\d+)", text)
        labels = {1: "unknown", 2: "charging", 3: "discharging", 4: "not_charging", 5: "full"}
        return (
            int(level.group(1)) if level else None,
            labels.get(int(status.group(1)), f"code_{status.group(1)}") if status else None,
            round(int(temp.group(1)) / 10.0, 1) if temp else None,
        )

    @staticmethod
    def _parse_storage(text: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
        for line in reversed(text.splitlines()):
            fields = line.split()
            if len(fields) >= 4 and all(value.isdigit() for value in fields[1:4]):
                total, used, free = (int(value) * 1024 for value in fields[1:4])
                return total, used, free
        return None, None, None

    @staticmethod
    def _parse_meminfo(text: str) -> tuple[Optional[int], Optional[int]]:
        values = {match.group(1): int(match.group(2)) * 1024 for match in re.finditer(r"(?m)^(MemTotal|MemAvailable):\s*(\d+)\s*kB", text)}
        return values.get("MemTotal"), values.get("MemAvailable")

    def _record_error(self, snapshot: DeviceHealthSnapshot, field: str, message: str) -> None:
        snapshot.field_status[field] = HealthStatus.ERROR.value
        snapshot.errors.append({"field": field, "message": message[:500]})

    def _shell(self, snapshot: DeviceHealthSnapshot, field: str, command: str, *, timeout: float = 10.0) -> Optional[str]:
        try:
            result = self._adb.shell(command, serial=snapshot.serial, timeout=timeout, retries=0)
            if not getattr(result, "success", False):
                self._record_error(snapshot, field, str(getattr(result, "message", "ADB command failed")))
                return None
            snapshot.field_status[field] = HealthStatus.OK.value
            return self._output(result)
        except Exception as exc:
            self._record_error(snapshot, field, str(exc))
            return None

    def _property(self, snapshot: DeviceHealthSnapshot, field: str, name: str) -> Optional[str]:
        try:
            value = self._adb.get_property(name, serial=snapshot.serial, timeout=10.0)
            if not str(value).strip():
                snapshot.field_status[field] = HealthStatus.UNSUPPORTED.value
                return None
            snapshot.field_status[field] = HealthStatus.OK.value
            return str(value).strip()
        except Exception as exc:
            self._record_error(snapshot, field, str(exc))
            return None

    def collect_snapshot(self, serial: str) -> DeviceHealthSnapshot:
        """Obtiene un snapshot y lo persiste; nunca sustituye datos ausentes por cero."""
        target = self._adb.resolve_serial(serial)
        snapshot = DeviceHealthSnapshot(serial=target)
        try:
            device = next((item for item in self._adb.devices() if item.serial == target), None)
            snapshot.connection_type = "network" if device and getattr(device, "is_network", False) else "usb"
            snapshot.field_status["connection"] = HealthStatus.OK.value
        except Exception as exc:
            snapshot.connection_type = "network" if ":" in target else "usb"
            self._record_error(snapshot, "connection", str(exc))

        battery = self._shell(snapshot, "battery", "dumpsys battery")
        if battery is not None:
            snapshot.battery_level, snapshot.battery_status, snapshot.temperature_celsius = self._parse_battery(battery)
            if snapshot.battery_level is None:
                snapshot.field_status["battery"] = HealthStatus.PARTIAL.value
            if snapshot.temperature_celsius is None:
                snapshot.field_status["temperature"] = HealthStatus.UNSUPPORTED.value
            else:
                snapshot.field_status["temperature"] = HealthStatus.OK.value

        storage = self._shell(snapshot, "storage", "df -k /data")
        if storage is not None:
            snapshot.storage_total_bytes, snapshot.storage_used_bytes, snapshot.storage_free_bytes = self._parse_storage(storage)
            if snapshot.storage_total_bytes is None:
                snapshot.field_status["storage"] = HealthStatus.UNSUPPORTED.value

        memory = self._shell(snapshot, "ram", "cat /proc/meminfo")
        if memory is not None:
            snapshot.ram_total_bytes, snapshot.ram_available_bytes = self._parse_meminfo(memory)
            if snapshot.ram_total_bytes is None:
                snapshot.field_status["ram"] = HealthStatus.UNSUPPORTED.value

        snapshot.android_version = self._property(snapshot, "android_version", "ro.build.version.release")
        snapshot.manufacturer = self._property(snapshot, "manufacturer", "ro.product.manufacturer")
        snapshot.model = self._property(snapshot, "model", "ro.product.model")

        route = self._shell(snapshot, "network", "ip route")
        if route is not None:
            snapshot.network_status = "connected" if route else None
            if not route:
                snapshot.field_status["network"] = HealthStatus.UNSUPPORTED.value

        start = time.perf_counter()
        latency_result = self._shell(snapshot, "adb_latency", "echo health", timeout=5.0)
        if latency_result is not None:
            snapshot.adb_latency_ms = round((time.perf_counter() - start) * 1000, 2)

        snapshot.field_status["processes"] = HealthStatus.UNSUPPORTED.value
        failed = any(status == HealthStatus.ERROR.value for status in snapshot.field_status.values())
        partial = any(status == HealthStatus.PARTIAL.value for status in snapshot.field_status.values())
        snapshot.overall_status = HealthStatus.PARTIAL.value if failed or partial else HealthStatus.OK.value
        self._store.save_health_snapshot(snapshot.to_dict())
        return snapshot

    def get_history(self, serial: str, *, limit: int = 100, start_utc: Optional[str] = None,
                    end_utc: Optional[str] = None) -> List[DeviceHealthSnapshot]:
        return [DeviceHealthSnapshot.from_dict(item) for item in self._store.list_health_snapshots(
            serial, limit=limit, start_utc=start_utc, end_utc=end_utc,
        )]

    def get_trends(self, serial: str, *, days: int = 7) -> DeviceHealthTrends:
        start = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        history = self.get_history(serial, limit=10_000, start_utc=start)
        fields = {
            "battery_level": [item.battery_level for item in history],
            "storage_free_bytes": [item.storage_free_bytes for item in history],
            "adb_latency_ms": [item.adb_latency_ms for item in history],
        }
        trends: Dict[str, HealthTrend] = {}
        for name, raw_values in fields.items():
            values = [float(value) for value in raw_values if value is not None]
            trends[name] = HealthTrend(
                metric=name, samples=len(values), latest=values[0] if values else None,
                minimum=min(values) if values else None, maximum=max(values) if values else None,
                average=(sum(values) / len(values)) if values else None,
            )
        return DeviceHealthTrends(serial=serial, days=days, trends=trends)
