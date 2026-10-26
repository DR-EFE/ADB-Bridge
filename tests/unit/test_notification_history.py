"""El historial conserva el origen y separa registros de distintos equipos."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ui.services.notification_history import UNKNOWN_DEVICE, device_key, event_day, load_notification_history
from ui.pages import notifications_page
from ui_automation.monitor import NotificationMonitor
from ui_automation.notifications import ClassificationResult, NotificationItem


def test_history_keeps_devices_and_days_separate():
    with TemporaryDirectory() as temporary:
        folder = Path(temporary)
        daily = [
            {"device_serial": "phone-A", "package": "app", "title": "Uno", "text": "A", "timestamp": "2026-10-20T10:00:00"},
            {"device_serial": "phone-B", "package": "app", "title": "Dos", "text": "B", "timestamp": "2026-10-20T11:00:00"},
            {"package": "app", "title": "Antigua", "text": "C", "timestamp": "2026-10-19T09:00:00"},
            {"package": "app", "title": "Uno", "text": "A", "timestamp": "2026-10-20T10:00:00"},
        ]
        (folder / "notifications_2026-10-20.json").write_text(json.dumps(daily), encoding="utf-8")
        important = [{
            "device_serial": "phone-A", "detected_at": "2026-10-20T10:01:00", "tipo": "ALERTA",
            "notification": daily[0],
        }]
        (folder / "important_2026-10-20.json").write_text(json.dumps(important), encoding="utf-8")

        events = load_notification_history(folder)
        assert len(events) == 3  # La alerta no aparece también como notificación normal.
        assert {(event["device_serial"], event_day(event)) for event in events} == {
            ("phone-A", "2026-10-20"),
            ("phone-B", "2026-10-20"),
            (UNKNOWN_DEVICE, "2026-10-19"),
        }
        assert next(event for event in events if event["device_serial"] == "phone-A")["tipo"] == "ALERTA"


def test_same_device_id_groups_changed_wifi_endpoints():
    first = {"device_id": "physical-1", "device_serial": "192.168.1.2:5555"}
    second = {"device_id": "physical-1", "device_serial": "192.168.1.9:5555"}
    assert device_key(first) == device_key(second) == "physical-1"


def test_new_daily_record_includes_device_serial():
    with TemporaryDirectory() as temporary:
        monitor = NotificationMonitor(
            serial="phone-A", logs_dir=temporary, use_ai=False,
            enable_watchers=False, device_copy_path=None, device_id="physical-1",
        )
        item = NotificationItem(package="app", title="Hola", text="Mensaje", timestamp="2026-10-20T10:00:00")
        normal = ClassificationResult(importante=False, tipo="NORMAL", codigo=None, razon="", fuente="rules")
        with patch("ui_automation.monitor.classify_notification", return_value=normal), patch.object(monitor, "_enqueue_ai_classification"):
            monitor._process_new_notification(item)
        records = json.loads(monitor._daily_log_path().read_text(encoding="utf-8"))
        assert records[0]["device_serial"] == "phone-A"
        assert records[0]["device_id"] == "physical-1"
        monitor._ai_executor.shutdown(wait=False, cancel_futures=True)


def test_page_limits_rows_and_filters_device(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "notifications"
    folder.mkdir()
    monkeypatch.setattr(notifications_page, "NOTIFICATIONS_DIR", folder)
    records = [
        {"device_serial": "phone-A" if index % 2 else "phone-B", "package": "app",
         "title": str(index), "text": "mensaje", "timestamp": f"2026-10-20T10:{index:02d}:00"}
        for index in range(30)
    ]
    (folder / "notifications_2026-10-20.json").write_text(json.dumps(records), encoding="utf-8")
    device = type("Device", (), {"serial": "phone-A"})()
    page = notifications_page.NotificationsPage(device)
    assert len(page._all_events) == 30
    assert page._visible_count == 20
    assert page._btn_more in page._table_container.controls

    page._filter_device.value = "phone-A"
    page._on_filter_changed(None)
    assert len(page._filtered_events()) == 15
    assert page._btn_more not in page._table_container.controls
