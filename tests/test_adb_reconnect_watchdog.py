import threading
from unittest.mock import MagicMock, patch

from ui_automation import adb_manager
from ui_automation.device import DeviceManager


def test_watchdog_can_be_stopped_by_new_endpoint_after_reconnect():
    old_endpoint = "192.168.1.50:41000"
    new_endpoint = "192.168.1.50:42000"

    class StopAfterRecovery:
        def __init__(self):
            self.stopped = False
            self.waits = 0

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, _interval):
            self.waits += 1
            if self.waits == 1:
                return False
            assert adb_manager.is_watchdog_active(old_endpoint)
            assert adb_manager.is_watchdog_active(new_endpoint)
            adb_manager.stop_reconnect_watchdog(new_endpoint)
            return True

    stop_event = StopAfterRecovery()
    client = MagicMock()
    client.list.return_value = []
    with patch.dict(adb_manager._active_watchdogs, {old_endpoint: stop_event}, clear=True), \
         patch.object(adb_manager, "get_adb_client", return_value=client), \
         patch("ui.services.device_service.DeviceService") as service_type:
        service_type.return_value.reconnect_device.return_value = new_endpoint
        adb_manager._watchdog_loop(old_endpoint, 0, stop_event)
        service_type.return_value.reconnect_device.assert_called_once_with(old_endpoint)
        assert stop_event.is_set()
        assert not adb_manager._active_watchdogs


def test_watchdog_expiry_clears_active_registration():
    endpoint = "192.168.1.50:41000"

    class ImmediateCheck:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, _interval):
            return False

    stop_event = ImmediateCheck()
    client = MagicMock()
    client.list.return_value = []
    with patch.dict(adb_manager._active_watchdogs, {endpoint: stop_event}, clear=True), \
         patch.object(adb_manager, "get_adb_client", return_value=client), \
         patch.object(adb_manager.time, "monotonic", side_effect=[10.0, 11.0]):
        adb_manager._watchdog_loop(endpoint, 0, stop_event, max_duration=0.5)
        assert stop_event.is_set()
        assert not adb_manager.is_watchdog_active(endpoint)


def test_device_manager_rebinds_uiautomator_after_port_change():
    old_endpoint = "192.168.1.50:41000"
    new_endpoint = "192.168.1.50:42000"
    event = threading.Event()
    manager = DeviceManager(serial=old_endpoint)
    manager._watchdog_event = event
    manager._device = MagicMock(serial=old_endpoint)
    replacement = MagicMock(serial=new_endpoint)
    with patch.dict(adb_manager._watchdog_current_serial, {event: new_endpoint}, clear=True), \
         patch("ui_automation.device.ensure_uiautomator_ready") as ready, \
         patch("ui_automation.device.u2.connect", return_value=replacement) as connect:
        assert manager.get_device() is replacement
        ready.assert_called_once_with(serial=new_endpoint)
        connect.assert_called_once_with(new_endpoint)
        assert manager.serial == new_endpoint


def test_explicit_automation_reconnect_uses_recovered_port():
    old_endpoint = "192.168.1.50:41000"
    new_endpoint = "192.168.1.50:42000"
    event = threading.Event()
    manager = DeviceManager(serial=old_endpoint)
    manager._watchdog_event = event
    replacement = MagicMock(serial=new_endpoint)
    replacement.info = {"productName": "Phone"}
    client = MagicMock()
    with patch.dict(adb_manager._watchdog_current_serial, {event: new_endpoint}, clear=True), \
         patch("ui_automation.device.ensure_single_adb_server", return_value=client), \
         patch("ui_automation.device.ensure_uiautomator_ready"), \
         patch("ui_automation.device.u2.connect", return_value=replacement) as connect, \
         patch("ui_automation.device.start_reconnect_watchdog", return_value=event):
        assert manager.connect() is replacement
        client.connect.assert_called_once_with(new_endpoint, timeout=10.0)
        connect.assert_called_once_with(new_endpoint)
        assert manager.serial == new_endpoint
