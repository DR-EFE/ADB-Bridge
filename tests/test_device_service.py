import json
import hashlib
import threading
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.services.device_service import (
    DeviceRecord,
    DeviceService,
    DeviceStatus,
    EndpointRecord,
    ensure_devices_store_migrated,
    read_devices_store,
    save_devices_store,
)
from ui_automation.adb_manager import ensure_single_adb_server


class TestDeviceService(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.canonical_json = self.test_dir / "AndroidTools" / "devices.json"
        self.legacy_json = self.test_dir / "config" / "devices.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("ui.services.device_service.get_adb_client")
    def test_qr_pairing_saves_connect_service_for_later_reconnect(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": None, "Devices": []}, self.canonical_json)
        old_endpoint = "192.168.1.50:41000"
        new_endpoint = "192.168.1.50:42000"
        connect_name = "adb-phone-123._adb-tls-connect._tcp"
        qr_service = MagicMock()
        qr_service.pair.return_value = SimpleNamespace(
            endpoint=old_endpoint, service_name="androidtools-temporary"
        )
        client = MagicMock()
        client.connect.return_value = f"connected to {old_endpoint}"
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        mdns = [
            {"service_name": connect_name, "endpoint": old_endpoint, "host": "192.168.1.50"}
        ]
        with patch.object(service, "find_wireless_devices", return_value=mdns), \
             patch.object(service, "_endpoint_ready", return_value=True), \
             patch.object(service, "_connected_identity_hash", return_value="sha256:phone"):
            ok, _ = service.pair_wireless_qr(qr_service=qr_service, qr_payload=object())

        self.assertTrue(ok)
        saved = read_devices_store(self.canonical_json)["Devices"][0]
        self.assertEqual(connect_name, saved["MdnsServiceName"])
        self.assertEqual(connect_name, saved["Endpoints"][0]["MdnsServiceName"])

        with patch.object(service, "find_wireless_devices", return_value=[
                 {"service_name": connect_name, "endpoint": new_endpoint, "host": "192.168.1.50"}
             ]), patch.object(service, "_connect_recovery_candidate", return_value=(True, "")), \
             patch.object(service, "_verify_reconnect_identity", return_value=("verified", "sha256:phone")), \
             patch.object(service, "refresh_device_metadata"):
            result = service.auto_reconnect_saved_devices()[0]

        self.assertTrue(result.success)
        self.assertEqual(new_endpoint, result.endpoint)
        self.assertEqual("mdns", result.method)

    @patch("ui.services.device_service.get_adb_client")
    def test_old_qr_record_recovers_by_verified_identity(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "phone", "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "Mode": "wireless-debugging-tls",
            "MdnsServiceName": "androidtools-old-pairing", "PhysicalIdHash": "sha256:phone",
            "LastEndpoint": "192.168.1.50:41000",
        }]}, self.canonical_json)
        mock_get_client.return_value.list.return_value = []
        service = DeviceService(devices_json_path=self.canonical_json)
        services = [
            {"service_name": "other._adb-tls-connect._tcp", "endpoint": "192.168.1.60:42000"},
            {"service_name": "phone._adb-tls-connect._tcp", "endpoint": "192.168.1.50:42000"},
        ]
        with patch.object(service, "find_wireless_devices", return_value=services), \
             patch.object(service, "_connect_recovery_candidate", return_value=(True, "")), \
             patch.object(service, "_verify_reconnect_identity", side_effect=[
                 ("SERIAL_NO_COINCIDE", "sha256:other"), ("verified", "sha256:phone")
             ]), patch.object(service, "refresh_device_metadata"):
            result = service.auto_reconnect_saved_devices()[0]

        self.assertTrue(result.success)
        self.assertEqual("192.168.1.50:42000", result.endpoint)
        saved = read_devices_store(self.canonical_json)["Devices"][0]
        self.assertEqual("phone._adb-tls-connect._tcp", saved["MdnsServiceName"])

    @patch("ui.services.device_service.get_adb_client")
    def test_last_endpoint_requires_current_bssid_and_explicit_override(self, mock_get_client):
        endpoint = "192.168.1.30:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": endpoint,
            "Endpoints": [{"Endpoint": endpoint, "NetworkBssid": "aa:aa:aa:aa:aa:aa"}],
        }]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_current_network_bssid", return_value="bb:bb:bb:bb:bb:bb"), \
             patch.object(service, "_run_adb_host", return_value=endpoint), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            self.assertFalse(service.connect_device("phone").success)
            client.connect.assert_not_called()
            result = service.connect_device("phone", allow_cross_network=True)
        self.assertTrue(result.provisional)
        self.assertEqual(endpoint, read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])

    @patch("ui.services.device_service.get_adb_client")
    def test_last_endpoint_without_network_metadata_is_tried_automatically(self, mock_get_client):
        endpoint = "192.168.0.51:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555",
            "LastEndpoint": endpoint,
        }]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_run_adb_host", return_value=endpoint), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            result = service.connect_device("phone")
        self.assertTrue(result.success)
        client.connect.assert_called_once_with(endpoint, timeout=5.0)

    @patch("ui.services.device_service.get_adb_client")
    def test_mismatched_physical_identity_never_updates_saved_endpoint(self, mock_get_client):
        endpoint = "192.168.1.40:5555"
        expected = "sha256:" + hashlib.sha256(b"phone-a").hexdigest()
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone A", "Mode": "tcpip-5555", "LastEndpoint": endpoint,
            "PhysicalIdHash": expected,
            "Endpoints": [{"Endpoint": endpoint, "NetworkBssid": "aa:bb:cc:dd:ee:ff"}],
        }]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_current_network_bssid", return_value="aa:bb:cc:dd:ee:ff"), \
             patch.object(service, "_run_adb_host", return_value=endpoint), \
             patch.object(service, "_physical_identity", return_value="PHONE-B"):
            result = service.connect_device("phone")
        self.assertFalse(result.success)
        self.assertIn("SERIAL_NO_COINCIDE", result.error)
        self.assertEqual(expected, read_devices_store(self.canonical_json)["Devices"][0]["PhysicalIdHash"])

    @patch("ui.services.device_service.get_adb_client")
    def test_connected_response_with_wrong_transport_serial_is_rejected(self, mock_get_client):
        endpoint = "192.168.1.41:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": endpoint,
            "Endpoints": [{"Endpoint": endpoint, "NetworkBssid": "aa:bb:cc:dd:ee:ff"}],
        }]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_current_network_bssid", return_value="aa:bb:cc:dd:ee:ff"), \
             patch.object(service, "_run_adb_host", return_value="OTHER"), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            result = service.connect_device("phone")
        self.assertFalse(result.success)
        self.assertIn("SERIAL_NO_COINCIDE", result.error)
        self.assertEqual(endpoint, read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])

    @patch("ui.services.device_service.get_adb_client")
    def test_provisional_requires_explicit_binding_before_history_changes(self, mock_get_client):
        endpoint = "192.168.1.60:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": None,
            "Endpoints": [{"Endpoint": endpoint, "NetworkBssid": "aa:bb:cc:dd:ee:ff"}],
        }]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_current_network_bssid", return_value="aa:bb:cc:dd:ee:ff"), \
             patch.object(service, "_run_adb_host", return_value=endpoint), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            result = service.connect_device("phone")
            self.assertTrue(result.provisional)
            self.assertIsNone(read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])
            self.assertTrue(service.confirm_provisional_identity("phone").success)
        record = read_devices_store(self.canonical_json)["Devices"][0]
        self.assertEqual(endpoint, record["LastEndpoint"])
        self.assertEqual(DeviceService._physical_hash("PHONE-ID"), record["PhysicalIdHash"])

    @patch("ui.services.device_service.get_adb_client")
    def test_offline_recovery_disconnects_only_target_endpoint(self, mock_get_client):
        endpoint = "192.168.1.70:5555"
        other = "192.168.1.71:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": endpoint,
            "Endpoints": [{"Endpoint": endpoint, "NetworkBssid": "aa:bb:cc:dd:ee:ff"}],
        }]}, self.canonical_json)
        states = {endpoint: "offline", other: "device"}
        client = MagicMock()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": state})()
                                           for ep, state in states.items()]
        client.disconnect.side_effect = lambda ep: states.pop(ep, None)
        client.connect.side_effect = lambda ep, timeout: (states.update({ep: "device"}) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_current_network_bssid", return_value="aa:bb:cc:dd:ee:ff"), \
             patch.object(service, "_run_adb_host", return_value=endpoint), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            result = service.connect_device("phone")
        self.assertTrue(result.success)
        client.disconnect.assert_called_once_with(endpoint)
        self.assertEqual("device", states[other])

    def test_automatic_server_start_does_not_kill_existing_adb(self):
        client = MagicMock()
        client.server_version.side_effect = [OSError("not started"), 41]
        with patch("ui_automation.adb_manager.get_adb_client", return_value=client), \
             patch("ui_automation.adb_manager.socket.create_connection", side_effect=OSError("closed")), \
             patch("ui_automation.adb_manager.get_adb_executable", return_value="adb"), \
             patch("ui_automation.adb_manager.subprocess.run") as run:
            run.return_value.returncode = 0
            ensure_single_adb_server(force=False)
        self.assertEqual("start-server", run.call_args.args[0][1])

    @patch("ui.services.device_service.get_adb_client")
    def test_scanned_endpoint_updates_inventory_only_after_identity_validation(self, mock_get_client):
        endpoint = "192.168.1.88:40123"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "WifiMacAddress": "AABBCCDDEEFF",
            "LastEndpoint": "192.168.1.40:5555", "Endpoints": [],
        }]}, self.canonical_json)
        client = MagicMock()
        client.connect.return_value = f"connected to {endpoint}"
        client.list.return_value = [type("Record", (), {"serial": endpoint, "state": "device"})()]
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_verify_reconnect_identity", return_value=("verified", "sha256:identity")), \
             patch.object(service, "refresh_device_metadata"), \
             patch.object(service, "_current_network_bssid", return_value="aa:bb:cc:dd:ee:ff"):
            result = service.connect_discovered_endpoint(
                "phone", endpoint, trusted_mac_match=True, discovered_mac="AA:BB:CC:DD:EE:FF"
            )
        self.assertTrue(result.success)
        saved = read_devices_store(self.canonical_json)["Devices"][0]
        self.assertEqual(endpoint, saved["LastEndpoint"])
        self.assertEqual("AABBCCDDEEFF", saved["WifiMacAddress"])

    @patch("ui.services.device_service.get_adb_client")
    def test_scanned_endpoint_rejects_unverified_identity_without_mutating_inventory(self, mock_get_client):
        endpoint = "192.168.1.89:5555"
        original = "192.168.1.40:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [{
            "Id": "phone", "FriendlyName": "Phone", "WifiMacAddress": "AABBCCDDEEFF",
            "LastEndpoint": original, "Endpoints": [],
        }]}, self.canonical_json)
        client = MagicMock()
        client.connect.return_value = f"connected to {endpoint}"
        client.list.return_value = [type("Record", (), {"serial": endpoint, "state": "device"})()]
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_verify_reconnect_identity", return_value=("SERIAL_NO_COINCIDE", None)):
            result = service.connect_discovered_endpoint("phone", endpoint, trusted_mac_match=True)
        self.assertFalse(result.success)
        self.assertEqual(original, read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])

    def test_unresponsive_occupied_adb_port_requires_manual_restart(self):
        client = MagicMock()
        client.server_version.side_effect = OSError("bad protocol")
        with patch("ui_automation.adb_manager.get_adb_client", return_value=client), \
             patch("ui_automation.adb_manager.socket.create_connection", return_value=MagicMock()), \
             patch("ui_automation.adb_manager.subprocess.run") as run:
            with self.assertRaises(RuntimeError):
                ensure_single_adb_server(force=False)
            run.assert_not_called()

    @patch("ui.services.device_service.get_adb_client")
    def test_usb_conversion_preserves_identity_after_transport_restart(self, mock_get_client):
        endpoint = "192.168.1.50:5555"
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "phone", "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555", "LastEndpoint": None}
        ]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        usb_visible = {"USB123"}
        client.list.side_effect = lambda: [
            type("Record", (), {"serial": serial, "state": "device"})() for serial in (usb_visible | connected)
        ]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        usb = MagicMock()
        usb.getprop.side_effect = lambda key: {"ro.serialno": "PHYSICAL123", "ro.product.model": "Pixel"}.get(key, "")
        usb.shell.side_effect = lambda cmd: "default via 192.168.1.1 dev wlan0 src 192.168.1.50" if cmd == "ip route" else ""
        def restart_adbd(_port):
            usb_visible.clear()
            raise OSError("USB transport closed during adbd restart")
        usb.tcpip.side_effect = restart_adbd
        wifi = MagicMock()
        wifi.getprop.side_effect = lambda key: "PHYSICAL123" if key == "ro.serialno" else ""
        client.device.side_effect = lambda serial: usb if serial == "USB123" else wifi
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_run_adb_host", return_value=endpoint):
            ok, _ = service.convert_usb_to_wifi("USB123")
        self.assertTrue(ok)
        self.assertEqual(endpoint, read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])
        self.assertEqual("USB123", read_devices_store(self.canonical_json)["Devices"][0]["UsbSerial"])
        usb.tcpip.assert_called_once_with(5555)

    @patch("ui.services.device_service.get_adb_client")
    def test_usb_conversion_rejects_wrong_transport_serial(self, mock_get_client):
        endpoint = "192.168.1.50:5555"
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "phone", "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555", "LastEndpoint": None}
        ]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": serial, "state": "device"})()
                                           for serial in ({"USB123"} | connected)]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or "connected to device")
        usb = MagicMock()
        usb.getprop.side_effect = lambda key: "PHYSICAL123" if key == "ro.serialno" else "Pixel"
        usb.shell.return_value = "default via 192.168.1.1 dev wlan0 src 192.168.1.50"
        wifi = MagicMock()
        wifi.getprop.return_value = "PHYSICAL123"
        client.device.side_effect = lambda serial: usb if serial == "USB123" else wifi
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_run_adb_host", return_value="OTHER"), patch("ui.services.device_service.time.sleep"):
            ok, message = service.convert_usb_to_wifi("USB123")
        self.assertFalse(ok)
        self.assertIn("SERIAL_NO_COINCIDE", message)
        self.assertIsNone(read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])

    @patch("ui.services.device_service.get_adb_client")
    def test_usb_monitor_skips_tls_and_processes_two_legacy_phones(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "a", "Devices": [
            {"Id": "a", "FriendlyName": "A", "UsbSerial": "USB_A", "Mode": "tcpip-5555"},
            {"Id": "b", "FriendlyName": "B", "UsbSerial": "USB_B", "Mode": "tcpip-5555"},
            {"Id": "tls", "FriendlyName": "TLS", "UsbSerial": "USB_TLS", "Mode": "wireless-debugging-tls"},
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [type("Record", (), {"serial": s, "state": "device"})()
                                    for s in ("USB_A", "USB_B", "USB_TLS")]
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        completed = threading.Event()
        calls = []
        def convert(usb_serial, activate):
            calls.append(usb_serial)
            if len(calls) == 2:
                completed.set()
            return True, "ok"
        with patch.object(service, "convert_usb_to_wifi", side_effect=convert):
            service.check_and_auto_convert_usb()
            self.assertTrue(completed.wait(2))
        self.assertCountEqual(calls, ["USB_A", "USB_B"])

    @patch("ui.services.device_service.get_adb_client")
    def test_unauthorized_usb_does_not_restart_adbd(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555"}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [type("Record", (), {"serial": "USB123", "state": "unauthorized"})()]
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        ok, message = service.convert_usb_to_wifi("USB123")
        self.assertFalse(ok)
        self.assertIn("USB_UNAUTHORIZED", message)
        client.device.assert_not_called()

    def test_ip_fallback_uses_wifi_interface(self):
        device = MagicMock()
        device.shell.side_effect = lambda command: {
            "ip route": "default via 10.0.0.1 dev rmnet0 src 10.0.0.8",
            "ip addr": "1: lo: <LOOPBACK>\n inet 127.0.0.1/8\n2: wlan0: <UP>\n inet 192.168.1.42/24",
        }.get(command, "")
        self.assertEqual(DeviceService._usb_wifi_ip(device), "192.168.1.42")

    @patch("ui.services.device_service.get_adb_client")
    def test_history_other_network_requires_explicit_permission(self, mock_get_client):
        endpoint = "10.21.4.8:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": None,
             "Endpoints": [{"Endpoint": endpoint}]}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = []
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_endpoint_on_current_network", return_value=False):
            result = service.connect_device("phone")
        self.assertFalse(result.success)
        client.connect.assert_not_called()

    @patch("ui.services.device_service.get_adb_client")
    def test_connect_reply_does_not_count_without_device_state(self, mock_get_client):
        endpoint = "192.168.1.60:5555"
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": endpoint}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [type("Record", (), {"serial": endpoint, "state": "offline"})()]
        client.connect.return_value = f"connected to {endpoint}"
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        result = service.connect_device("phone")
        self.assertFalse(result.success)
        self.assertFalse(read_devices_store(self.canonical_json)["Devices"][0].get("Endpoints"))

    @patch("ui.services.device_service.get_adb_client")
    def test_active_wifi_for_same_phone_skips_tcpip_restart(self, mock_get_client):
        endpoint = "192.168.1.70:5555"
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "phone", "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555", "LastEndpoint": None}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [type("Record", (), {"serial": serial, "state": "device"})()
                                    for serial in ("USB123", endpoint)]
        usb = MagicMock()
        wifi = MagicMock()
        usb.getprop.side_effect = lambda key: "PHYSICAL123" if key == "ro.serialno" else "Pixel"
        wifi.getprop.side_effect = lambda key: "PHYSICAL123" if key == "ro.serialno" else "Pixel"
        client.device.side_effect = lambda serial: usb if serial == "USB123" else wifi
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_run_adb_host", return_value=endpoint):
            ok, _ = service.convert_usb_to_wifi("USB123")
        self.assertTrue(ok)
        usb.tcpip.assert_not_called()
        self.assertEqual(read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"], endpoint)

    @patch("ui.services.device_service.get_adb_client")
    def test_history_on_current_bssid_can_reconnect(self, mock_get_client):
        endpoint = "192.168.1.80:5555"
        bssid = "aa:bb:cc:dd:ee:ff"
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": None,
             "Endpoints": [{"Endpoint": endpoint, "NetworkBssid": bssid}]}
        ]}, self.canonical_json)
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("Record", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or f"connected to {ep}")
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "_current_network_bssid", return_value=bssid), patch.object(service, "refresh_device_metadata", return_value=None), \
             patch.object(service, "_run_adb_host", return_value=endpoint), patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            result = service.connect_device("phone")
        self.assertTrue(result.success)
        self.assertTrue(result.provisional)
        self.assertEqual(result.endpoint, endpoint)

    @patch("ui.services.device_service.get_adb_client")
    def test_same_usb_is_not_converted_twice_during_concurrent_scan(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555"}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [type("Record", (), {"serial": "USB123", "state": "device"})()]
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        started = threading.Event()
        release = threading.Event()
        calls = []
        def convert(usb_serial, activate):
            calls.append(usb_serial)
            started.set()
            release.wait(2)
            return True, "ok"
        with patch.object(service, "convert_usb_to_wifi", side_effect=convert):
            service.check_and_auto_convert_usb()
            self.assertTrue(started.wait(2))
            service.check_and_auto_convert_usb()
            release.set()
        self.assertEqual(calls, ["USB123"])

    @patch("ui.services.device_service.get_adb_client")
    def test_locked_phone_without_readable_identity_is_not_restarted(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555"}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [type("Record", (), {"serial": "USB123", "state": "device"})()]
        device = MagicMock()
        device.getprop.side_effect = RuntimeError("locked")
        device.shell.return_value = "null"
        client.device.return_value = device
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        ok, message = service.convert_usb_to_wifi("USB123")
        self.assertFalse(ok)
        self.assertIn("SERIAL_NO_COINCIDE", message)
        device.tcpip.assert_not_called()

    @patch("ui.services.device_service.get_adb_client")
    def test_legacy_record_ignores_unrelated_tls_mdns_advertisement(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "Mode": "tcpip-5555", "LastEndpoint": None}
        ]}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = []
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "find_wireless_devices", return_value=[
            {"service_name": "other._adb-tls-connect._tcp", "host": "192.168.1.90", "endpoint": "192.168.1.90:41234"}
        ]) as discover:
            result = service.connect_device("phone")
        self.assertFalse(result.success)
        discover.assert_not_called()

    @patch("ui.services.device_service.get_adb_client")
    def test_wifi_search_yields_when_usb_conversion_is_requested(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "Devices": [
            {"Id": "phone", "FriendlyName": "Phone", "UsbSerial": "USB123", "Mode": "tcpip-5555",
             "LastEndpoint": "192.168.1.90:5555"}
        ]}, self.canonical_json)
        service = DeviceService(devices_json_path=self.canonical_json)
        client = MagicMock()
        def list_with_usb_event():
            service._usb_priority.add("USB123")
            return []
        client.list.side_effect = list_with_usb_event
        mock_get_client.return_value = client
        result = service.connect_device("phone")
        self.assertEqual(result.error, "USB_PRIORITY")
        client.connect.assert_not_called()

    def test_migration_from_legacy_config(self):
        """Verifica la migración automática única desde config/devices.json al archivo canónico."""
        self.legacy_json.parent.mkdir(parents=True, exist_ok=True)
        legacy_data = {
            "SchemaVersion": 1,
            "CurrentDeviceId": "dev-legacy-1",
            "Devices": [
                {
                    "Id": "dev-legacy-1",
                    "FriendlyName": "Legacy Galaxy",
                    "UsbSerial": "RZCX123",
                    "LastEndpoint": "192.168.1.50:5555",
                    "Mode": "tcpip-5555",
                }
            ],
        }
        self.legacy_json.write_text(json.dumps(legacy_data), encoding="utf-8")

        self.assertFalse(self.canonical_json.is_file())
        ensure_devices_store_migrated(
            canonical_path=self.canonical_json,
            legacy_path=self.legacy_json,
        )
        self.assertTrue(self.canonical_json.is_file())

        migrated = json.loads(self.canonical_json.read_text(encoding="utf-8"))
        self.assertEqual(migrated["CurrentDeviceId"], "dev-legacy-1")
        self.assertEqual(len(migrated["Devices"]), 1)
        self.assertEqual(migrated["Devices"][0]["FriendlyName"], "Legacy Galaxy")

    def test_extended_device_record_fields_preservation(self):
        """Verifica que todos los campos del modelo extendido se conserven intactos al guardar y leer."""
        now_iso = datetime.now(timezone.utc).isoformat()
        sample_record = {
            "Id": "test-dev-uuid",
            "FriendlyName": "Samsung Galaxy A55",
            "UsbSerial": "RZCXB1E27KZ",
            "WifiMacAddress": "00:11:22:33:44:55",
            "LastEndpoint": "192.168.137.185:5555",
            "Mode": "wireless-debugging-tls",
            "MdnsServiceName": "adb-tls-connect-test._tcp",
            "LastIp": "192.168.137.185",
            "LastConnectedUtc": now_iso,
            "LastSeenUtc": now_iso,
            "Model": "SM-A556E",
            "AndroidVersion": "14",
            "Battery": 85,
            "IsPaired": True,
            "Endpoints": [
                {
                    "Endpoint": "192.168.137.185:5555",
                    "ConnectedUtc": now_iso,
                    "MdnsServiceName": "adb-tls-connect-test._tcp",
                    "Source": "mdns",
                }
            ],
            "NetworkProfiles": [
                {
                    "Id": "prof-1",
                    "Name": "Casa Wi-Fi",
                    "PreferredEndpoint": "192.168.137.185:5555",
                }
            ],
        }

        store = {
            "SchemaVersion": 1,
            "CurrentDeviceId": "test-dev-uuid",
            "Devices": [sample_record],
        }
        save_devices_store(store, self.canonical_json)

        service = DeviceService(devices_json_path=self.canonical_json)
        saved_list = service.list_saved_devices()
        self.assertEqual(len(saved_list), 1)

        dev = saved_list[0]
        self.assertEqual(dev.id, "test-dev-uuid")
        self.assertEqual(dev.friendly_name, "Samsung Galaxy A55")
        self.assertEqual(dev.usb_serial, "RZCXB1E27KZ")
        self.assertEqual(dev.wifi_mac_address, "00:11:22:33:44:55")
        self.assertEqual(dev.last_endpoint, "192.168.137.185:5555")
        self.assertEqual(dev.mode, "wireless-debugging-tls")
        self.assertEqual(dev.mdns_service_name, "adb-tls-connect-test._tcp")
        self.assertEqual(dev.last_ip, "192.168.137.185")
        self.assertEqual(dev.model, "SM-A556E")
        self.assertEqual(dev.android_version, "14")
        self.assertEqual(dev.battery, 85)
        self.assertTrue(dev.is_paired)
        self.assertEqual(len(dev.endpoints), 1)
        self.assertEqual(dev.endpoints[0]["Source"], "mdns")
        self.assertEqual(len(dev.network_profiles), 1)

    def test_endpoints_history_cap_at_ten(self):
        """Verifica que el historial de endpoints conserve como máximo 10 registros exitosos."""
        service = DeviceService(devices_json_path=self.canonical_json)
        record = {
            "Id": "dev-history-1",
            "FriendlyName": "Phone History",
            "Endpoints": [],
        }

        for i in range(15):
            service._record_successful_endpoint(
                record,
                f"192.168.1.{100 + i}:5555",
                source="tcpip",
            )

        self.assertEqual(len(record["Endpoints"]), 10)
        # El más reciente (114) debe estar en la primera posición
        self.assertEqual(record["Endpoints"][0]["Endpoint"], "192.168.1.114:5555")
        # El más antiguo retenido (105) en la última posición
        self.assertEqual(record["Endpoints"][9]["Endpoint"], "192.168.1.105:5555")

    def test_deduplication_priority(self):
        """
        Verifica la prioridad de deduplicación:
        1. Mismo UsbSerial
        2. Mismo MdnsServiceName
        3. Mismo endpoint
        """
        service = DeviceService(devices_json_path=self.canonical_json)
        saved_records = [
            {
                "Id": "dev-1",
                "FriendlyName": "Phone 1",
                "UsbSerial": "USB_SERIAL_1",
                "MdnsServiceName": "adb-svc-1",
                "LastEndpoint": "192.168.1.10:5555",
                "Endpoints": [{"Endpoint": "192.168.1.10:5555"}],
            },
            {
                "Id": "dev-2",
                "FriendlyName": "Phone 2",
                "UsbSerial": "USB_SERIAL_2",
                "MdnsServiceName": "adb-svc-2",
                "LastEndpoint": "192.168.1.20:5555",
                "Endpoints": [{"Endpoint": "192.168.1.20:5555"}],
            },
        ]

        # 1. Match por UsbSerial
        match_usb = service._match_online_to_saved("USB_SERIAL_1", None, saved_records)
        self.assertIsNotNone(match_usb)
        self.assertEqual(match_usb["Id"], "dev-1")

        # 2. Match por MdnsServiceName
        match_mdns = service._match_online_to_saved("192.168.1.99:5555", "adb-svc-2", saved_records)
        self.assertIsNotNone(match_mdns)
        self.assertEqual(match_mdns["Id"], "dev-2")

        # 3. Match por Endpoint
        match_ep = service._match_online_to_saved("192.168.1.10:5555", None, saved_records)
        self.assertIsNotNone(match_ep)
        self.assertEqual(match_ep["Id"], "dev-1")

    @patch("ui.services.device_service.get_adb_client")
    def test_safe_selection_single_device(self, mock_get_client):
        """Con 1 solo dispositivo en estado 'device', se selecciona automáticamente de forma segura."""
        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.serial = "192.168.1.185:5555"
        mock_info.state = "device"
        mock_client.list.return_value = [mock_info]

        mock_dev = MagicMock()
        mock_dev.getprop.return_value = "Galaxy A55"
        mock_dev.shell.return_value = "level: 80"
        mock_client.device.return_value = mock_dev
        mock_get_client.return_value = mock_client

        service = DeviceService(devices_json_path=self.canonical_json)
        status = service.get_status()

        self.assertFalse(status.requires_selection)
        self.assertTrue(status.connected)
        self.assertEqual(status.serial, "192.168.1.185:5555")

    @patch("ui.services.device_service.get_adb_client")
    def test_safe_selection_multiple_devices_requires_selection(self, mock_get_client):
        """Con varios dispositivos físicos listos, NO selecciona silenciosamente; exige selección obligatoria."""
        mock_client = MagicMock()
        dev1 = MagicMock()
        dev1.serial = "USB_SERIAL_A"
        dev1.state = "device"

        dev2 = MagicMock()
        dev2.serial = "192.168.1.200:5555"
        dev2.state = "device"

        mock_client.list.return_value = [dev1, dev2]

        mock_dev_a = MagicMock()
        mock_dev_a.getprop.return_value = "PHONE-A-SERIAL"
        mock_dev_a.shell.return_value = "level: 50"
        mock_dev_b = MagicMock()
        mock_dev_b.getprop.return_value = "PHONE-B-SERIAL"
        mock_dev_b.shell.return_value = "level: 50"
        mock_client.device.side_effect = lambda serial: mock_dev_a if serial == "USB_SERIAL_A" else mock_dev_b
        mock_get_client.return_value = mock_client

        service = DeviceService(devices_json_path=self.canonical_json)
        # Sin selección explícita previa
        service._explicitly_selected = False
        service.serial = None

        status = service.get_status()

        self.assertTrue(status.requires_selection)
        self.assertFalse(status.connected)
        self.assertIn("Hay 2 dispositivos", status.error)

        # Cuando el usuario selecciona uno explícitamente:
        ok, msg = service.set_active_by_serial("USB_SERIAL_A")
        self.assertTrue(ok)
        self.assertTrue(service._explicitly_selected)

        status_after = service.get_status()
        self.assertFalse(status_after.requires_selection)
        self.assertTrue(status_after.connected)
        self.assertEqual(status_after.serial, "USB_SERIAL_A")

    @patch("ui.services.device_service.get_adb_client")
    def test_usb_and_wifi_same_phone_are_one_device(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": None, "Devices": []}, self.canonical_json)
        client = MagicMock()
        client.list.return_value = [
            SimpleNamespace(serial="USB-A", state="device"),
            SimpleNamespace(serial="192.168.1.50:5555", state="device"),
        ]
        dev = MagicMock()
        dev.getprop.return_value = "REAL-PHONE-SERIAL"
        dev.shell.return_value = "level: 50"
        client.device.return_value = dev
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch.object(service, "find_wireless_devices", return_value=[]):
            status = service.get_status()
        self.assertTrue(status.connected)
        self.assertFalse(status.requires_selection)
        self.assertEqual(1, len(status.available_devices))
        cards = service.get_management_data()["cards"]
        self.assertEqual(1, len(cards), cards)

    @patch("ui.services.device_service.get_adb_client")
    def test_unlock_prefers_pin_and_never_enters_password_text(self, mock_get_client):
        client = MagicMock()
        client.list.return_value = [SimpleNamespace(serial="USB-A", state="device")]
        dev = MagicMock()
        dev.shell.side_effect = lambda command: (
            "Physical size: 1080x1920" if command == "wm size" else
            '<node resource-id="com.android.systemui:id/pinEntry" />'
            '<node resource-id="com.android.systemui:id/passwordEntry" />'
            if command == "uiautomator dump /dev/tty" else ""
        )
        client.device.return_value = dev
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch("ui.services.device_service.time.sleep"):
            result = service.unlock_device("USB-A", "1234")
        self.assertTrue(result.success)
        commands = [call.args[0] for call in dev.shell.call_args_list]
        self.assertIn("input keyevent KEYCODE_WAKEUP", commands)
        self.assertIn("input swipe 540 1536 540 480 350", commands)
        self.assertEqual(4, len([command for command in commands if command.startswith("input keyevent KEYCODE_") and command[-1].isdigit()]))
        self.assertFalse(any(command.startswith("input text ") for command in commands))

    def test_unlock_rejects_empty_credential(self):
        service = DeviceService(devices_json_path=self.canonical_json)
        self.assertFalse(service.unlock_device("USB-A", "").success)

    @patch("ui.services.device_service.get_adb_client")
    def test_unlock_password_and_unknown_prompt(self, mock_get_client):
        client = MagicMock()
        client.list.return_value = [SimpleNamespace(serial="USB-A", state="device")]
        dev = MagicMock()
        client.device.return_value = dev
        mock_get_client.return_value = client
        service = DeviceService(devices_json_path=self.canonical_json)
        with patch("ui.services.device_service.time.sleep"):
            dev.shell.side_effect = lambda command: (
                '<node resource-id="com.android.systemui:id/passwordEntry" />'
                if command == "uiautomator dump /dev/tty" else ""
            )
            self.assertTrue(service.unlock_device("USB-A", "abc 123").success)
            self.assertIn("input text abc%s123", [call.args[0] for call in dev.shell.call_args_list])
            dev.shell.reset_mock()
            dev.shell.return_value = ""
            dev.shell.side_effect = None
            self.assertFalse(service.unlock_device("USB-A", "abc123").success)
            self.assertFalse(any(call.args[0].startswith("input text ") for call in dev.shell.call_args_list))

    def test_device_actions_crud(self):
        """Verifica las acciones públicas de DeviceService: rename, set_primary, disconnect, remove."""
        initial_store = {
            "SchemaVersion": 1,
            "CurrentDeviceId": "dev-action-1",
            "Devices": [
                {
                    "Id": "dev-action-1",
                    "FriendlyName": "Original Name",
                    "UsbSerial": "USB_1",
                    "LastEndpoint": "192.168.1.10:5555",
                    "Mode": "tcpip-5555",
                },
                {
                    "Id": "dev-action-2",
                    "FriendlyName": "Second Phone",
                    "UsbSerial": "USB_2",
                    "LastEndpoint": "192.168.1.20:5555",
                    "Mode": "tcpip-5555",
                },
            ],
        }
        save_devices_store(initial_store, self.canonical_json)

        service = DeviceService(devices_json_path=self.canonical_json)

        # 1. get_device_details
        det = service.get_device_details("dev-action-1")
        self.assertIsNotNone(det)
        self.assertEqual(det.friendly_name, "Original Name")

        # 2. rename_device
        res_rename = service.rename_device("dev-action-1", "Renamed Phone")
        self.assertTrue(res_rename.success)
        det_renamed = service.get_device_details("dev-action-1")
        self.assertEqual(det_renamed.friendly_name, "Renamed Phone")

        # Validar no vacío
        res_empty = service.rename_device("dev-action-1", "   ")
        self.assertFalse(res_empty.success)

        # 3. Supervisor hook y disconnect
        supervisor_stopped = []
        service.register_supervisor("dev-action-1", on_stop=lambda: supervisor_stopped.append(True))
        res_disc = service.disconnect_device("dev-action-1")
        self.assertTrue(res_disc.success)
        self.assertTrue(supervisor_stopped)

        # 4. remove_device
        res_rem = service.remove_device("dev-action-1")
        self.assertTrue(res_rem.success)
        self.assertIsNone(service.get_device_details("dev-action-1"))

        # Como quedaba solo dev-action-2, debe seleccionarse automáticamente como único listo
        store_after = read_devices_store(self.canonical_json)
        self.assertEqual(store_after.get("CurrentDeviceId"), "dev-action-2")
        self.assertEqual(service.serial, "192.168.1.20:5555")

    @patch("ui.services.device_service.get_adb_client")
    def test_auto_reconnect_keeps_primary_active_and_notifies_endpoint_change(self, mock_get_client):
        """Un registro secundario conectado no sustituye el endpoint elegido."""
        save_devices_store(
            {
                "SchemaVersion": 1,
                "CurrentDeviceId": "primary",
                "Devices": [
                    {"Id": "primary", "FriendlyName": "Principal", "LastEndpoint": "192.168.1.10:5555", "LastIp": "192.168.1.10", "Mode": "wireless-debugging-tls", "MdnsServiceName": "primary"},
                    {"Id": "secondary", "FriendlyName": "Secundario", "LastEndpoint": "192.168.1.20:5555", "LastIp": "192.168.1.20", "Mode": "wireless-debugging-tls", "MdnsServiceName": "secondary"},
                ],
            },
            self.canonical_json,
        )
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("AdbRecord", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or "connected to device")
        mock_get_client.return_value = client
        service = DeviceService(serial="192.168.1.10:5555", devices_json_path=self.canonical_json)
        changed = []
        service.add_device_change_listener(lambda: changed.append(service.serial))

        with patch.object(
            service,
            "find_wireless_devices",
            return_value=[
                {"service_name": "primary", "host": "192.168.1.10", "endpoint": "192.168.1.10:46617"},
                {"service_name": "secondary", "host": "192.168.1.20", "endpoint": "192.168.1.20:46618"},
            ],
        ), patch.object(service, "refresh_device_metadata", return_value=None), \
             patch.object(service, "_run_adb_host", side_effect=lambda *args, **kwargs: args[1]), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            results = service.auto_reconnect_saved_devices()

        self.assertEqual(2, sum(result.success for result in results))
        self.assertEqual("192.168.1.10:46617", service.serial)
        self.assertEqual(["192.168.1.10:46617"], changed)
        self.assertEqual("primary", read_devices_store(self.canonical_json)["CurrentDeviceId"])
        self.assertTrue(all(result.provisional for result in results))
        self.assertEqual("192.168.1.10:5555", read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])

    @patch("ui.services.device_service.get_adb_client")
    def test_reconnect_device_returns_new_endpoint_for_transfer(self, mock_get_client):
        save_devices_store(
            {
                "SchemaVersion": 1,
                "CurrentDeviceId": "phone",
                "Devices": [{"Id": "phone", "FriendlyName": "Phone", "LastEndpoint": "192.168.1.50:43863", "LastIp": "192.168.1.50", "Mode": "wireless-debugging-tls", "MdnsServiceName": "phone"}],
            },
            self.canonical_json,
        )
        client = MagicMock()
        connected = set()
        client.list.side_effect = lambda: [type("AdbRecord", (), {"serial": ep, "state": "device"})() for ep in connected]
        client.connect.side_effect = lambda ep, timeout: (connected.add(ep) or "connected to device")
        mock_get_client.return_value = client
        service = DeviceService(serial="192.168.1.50:43863", devices_json_path=self.canonical_json)

        with patch.object(service, "find_wireless_devices", return_value=[{"service_name": "phone", "host": "192.168.1.50", "endpoint": "192.168.1.50:46617"}]), \
             patch.object(service, "refresh_device_metadata", return_value=None), \
             patch.object(service, "_run_adb_host", return_value="192.168.1.50:46617"), \
             patch.object(service, "_physical_identity", return_value="PHONE-ID"):
            endpoint = service.reconnect_device("192.168.1.50:43863")

        self.assertEqual("192.168.1.50:46617", endpoint)
        self.assertEqual(endpoint, service.serial)

    def test_transfer_reconnect_of_secondary_does_not_change_primary(self):
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "primary", "Devices": [
            {"Id": "primary", "LastEndpoint": "192.168.1.10:41000"},
            {"Id": "secondary", "LastEndpoint": "192.168.1.20:42000"},
        ]}, self.canonical_json)
        service = DeviceService(serial="192.168.1.10:41000", devices_json_path=self.canonical_json)
        with patch.object(service, "connect_device", return_value=SimpleNamespace(
            success=True, endpoint="192.168.1.20:43000"
        )) as connect:
            endpoint = service.reconnect_device("192.168.1.20:42000")
        self.assertEqual("192.168.1.20:43000", endpoint)
        connect.assert_called_once_with("secondary", activate=False)

    @patch("ui.services.device_service.get_adb_client")
    def test_wifi_search_recovers_old_qr_record_by_identity(self, mock_get_client):
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "phone", "Devices": [
            {"Id": "phone", "MdnsServiceName": "old-qr-pairing", "PhysicalIdHash": "sha256:phone"},
        ]}, self.canonical_json)
        service = DeviceService(devices_json_path=self.canonical_json)
        mdns = [{"service_name": "phone._adb-tls-connect._tcp", "endpoint": "192.168.1.50:42000"}]
        with patch.object(service, "find_wireless_devices", return_value=mdns), \
             patch.object(service, "_connect_recovery_candidate", return_value=(True, "")), \
             patch.object(service, "_verify_reconnect_identity", return_value=("verified", "sha256:phone")):
            result = service.discover_and_reconnect_wireless()
        self.assertTrue(result.success)
        self.assertEqual("192.168.1.50:42000", result.endpoint)
        self.assertEqual("phone._adb-tls-connect._tcp", read_devices_store(self.canonical_json)["Devices"][0]["MdnsServiceName"])

    @patch("ui.services.device_service.get_adb_client")
    def test_manual_wifi_endpoint_must_match_saved_identity(self, mock_get_client):
        old_endpoint = "192.168.1.50:41000"
        new_endpoint = "192.168.1.50:42000"
        save_devices_store({"SchemaVersion": 1, "CurrentDeviceId": "phone", "Devices": [{
            "Id": "phone", "LastEndpoint": old_endpoint, "PhysicalIdHash": "sha256:phone",
        }]}, self.canonical_json)
        client = MagicMock()
        client.connect.return_value = f"connected to {new_endpoint}"
        mock_get_client.return_value = client
        service = DeviceService(serial=old_endpoint, devices_json_path=self.canonical_json)
        with patch.object(service, "_endpoint_ready", return_value=True), \
             patch.object(service, "_verify_reconnect_identity", side_effect=[
                 ("SERIAL_NO_COINCIDE", "sha256:other"), ("verified", "sha256:phone")
             ]):
            rejected = service.discover_and_reconnect_wireless(new_endpoint)
            self.assertFalse(rejected.success)
            self.assertEqual(old_endpoint, service.serial)
            self.assertEqual(old_endpoint, read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])
            accepted = service.discover_and_reconnect_wireless(new_endpoint)
        self.assertTrue(accepted.success)
        self.assertEqual(new_endpoint, service.serial)
        self.assertEqual(new_endpoint, read_devices_store(self.canonical_json)["Devices"][0]["LastEndpoint"])

    @patch("ui.services.device_service.get_adb_client")
    @patch("ui.services.device_service.get_adb_executable")
    def test_run_technical_diagnostics_all_fields(self, mock_exe, mock_client):
        """Verifica que run_technical_diagnostics reporte los 12 puntos requeridos."""
        mock_exe.return_value = "C:\\fake\\adb.exe"
        fake_client = MagicMock()
        fake_client.server_version.return_value = 41

        mock_dev = MagicMock()
        mock_dev.serial = "192.168.1.150:5555"
        mock_dev.get_state.return_value = "device"
        mock_dev.getprop.side_effect = lambda k: {
            "ro.product.model": "Pixel 7 Pro",
            "ro.product.manufacturer": "Google",
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "ro.product.cpu.abi": "arm64-v8a",
        }.get(k, "")
        mock_dev.wlan_ip.return_value = "192.168.1.150"
        mock_dev.shell.side_effect = lambda cmd: {
            "dumpsys battery": "Current Battery Service state:\n  level: 88\n  status: 2",
            "df -h /sdcard": "Filesystem      Size  Used Avail Use% Mounted on\n/dev/fuse       112G   42G   70G  38% /sdcard",
        }.get(cmd, "")

        fake_client.device_list.return_value = [mock_dev]
        fake_client.device.return_value = mock_dev
        mock_client.return_value = fake_client

        service = DeviceService(devices_json_path=self.canonical_json)
        service.serial = "192.168.1.150:5555"

        with patch.object(service, "find_wireless_devices", return_value=[{"service_name": "adb-1234", "endpoint": "192.168.1.150:41234"}]), \
             patch.object(service, "ping_device", return_value=(True, "Ping exitoso (22ms)")):
            rep = service.run_technical_diagnostics()

        # 1. Versión ADB
        self.assertTrue(rep.adb_version)
        # 2. Ruta ejecutable
        self.assertEqual(rep.adb_path, "C:\\fake\\adb.exe")
        # 3. Estado servidor
        self.assertIn("En ejecución", rep.adb_server_status)
        self.assertEqual(rep.adb_server_port, 5037)
        # 4. Dispositivos detectados
        self.assertEqual(len(rep.detected_devices), 1)
        self.assertEqual(rep.detected_devices[0]["serial"], "192.168.1.150:5555")
        # 5. Estado autorización
        self.assertEqual(rep.authorization_status, "device")
        # 6. Estado mDNS
        self.assertIn("Activo", rep.mdns_status)
        self.assertEqual(len(rep.mdns_services), 1)
        # 7. IP PC
        self.assertTrue(rep.pc_ip)
        # 8. IP Dispositivo
        self.assertEqual(rep.device_ip, "192.168.1.150")
        # 9. Puertos utilizados
        self.assertEqual(rep.ports_used.get("device_port"), 5555)
        self.assertEqual(rep.ports_used.get("adb_server_port"), 5037)
        # 10. Prueba de conectividad
        self.assertTrue(rep.connectivity_test.get("icmp_ping_ok"))
        # 11. Versión de Android
        self.assertEqual(rep.android_version, "14")
        self.assertEqual(rep.sdk_api, "34")
        self.assertEqual(rep.device_model, "Pixel 7 Pro")
        # 12. Espacio libre y batería
        self.assertEqual(rep.battery_level, 88)
        self.assertEqual(rep.battery_status, "Cargando")
        self.assertIn("70G", rep.storage_phone)
        self.assertTrue(rep.storage_pc)


if __name__ == "__main__":
    unittest.main()

