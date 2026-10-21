from datetime import datetime, timezone
from pathlib import Path

from ui.services.database import SQLiteStore
from ui.services.device_health_models import DeviceHealthSnapshot, HealthStatus
from ui.services.device_health_service import DeviceHealthService


class Result:
    def __init__(self, success=True, output="", message=""):
        self.success = success
        self.output = output
        self.message = message


class Device:
    serial = "192.168.1.50:5555"
    is_network = True


class FakeAdb:
    def resolve_serial(self, serial):
        return serial

    def devices(self):
        return [Device()]

    def get_property(self, name, **kwargs):
        return {
            "ro.build.version.release": "15",
            "ro.product.manufacturer": "Google",
            "ro.product.model": "Pixel Test",
        }[name]

    def shell(self, command, **kwargs):
        outputs = {
            "dumpsys battery": "level: 81\nstatus: 2\ntemperature: 315\n",
            "df -k /data": "Filesystem 1K-blocks Used Available Use% Mounted on\n/data 1000 400 600 40% /data\n",
            "cat /proc/meminfo": "MemTotal: 8000 kB\nMemAvailable: 3000 kB\n",
            "ip route": "default via 192.168.1.1 dev wlan0\n",
            "echo health": "health\n",
        }
        return Result(output=outputs[command])


def test_collects_and_persists_complete_snapshot(tmp_path):
    service = DeviceHealthService(FakeAdb(), SQLiteStore(tmp_path / "health.db"))
    snapshot = service.collect_snapshot("192.168.1.50:5555")
    assert snapshot.battery_level == 81
    assert snapshot.temperature_celsius == 31.5
    assert snapshot.storage_free_bytes == 600 * 1024
    assert snapshot.ram_available_bytes == 3000 * 1024
    assert snapshot.connection_type == "network"
    assert snapshot.overall_status == HealthStatus.OK.value
    assert len(service.get_history(snapshot.serial)) == 1


def test_snapshot_is_partial_when_a_command_fails(tmp_path):
    class PartialAdb(FakeAdb):
        def shell(self, command, **kwargs):
            if command == "cat /proc/meminfo":
                return Result(False, message="permission denied")
            return super().shell(command, **kwargs)

    snapshot = DeviceHealthService(PartialAdb(), SQLiteStore(tmp_path / "health.db")).collect_snapshot("192.168.1.50:5555")
    assert snapshot.ram_total_bytes is None
    assert snapshot.field_status["ram"] == HealthStatus.ERROR.value
    assert snapshot.overall_status == HealthStatus.PARTIAL.value


def test_history_trends_use_persisted_samples(tmp_path):
    store = SQLiteStore(tmp_path / "health.db")
    serial = "usb-1"
    for index, battery in enumerate((30, 70, 50)):
        item = DeviceHealthSnapshot(
            serial=serial, snapshot_id=f"snapshot-{index}", battery_level=battery,
            storage_free_bytes=(index + 1) * 100, adb_latency_ms=float(index + 1),
            overall_status=HealthStatus.OK.value,
        )
        store.save_health_snapshot(item.to_dict())
    trends = DeviceHealthService(FakeAdb(), store).get_trends(serial, days=1)
    battery = trends.trends["battery_level"]
    assert battery.samples == 3
    assert battery.latest == 50.0
    assert battery.minimum == 30.0
    assert battery.maximum == 70.0
    assert round(battery.average, 2) == 50.0
