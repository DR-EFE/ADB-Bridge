import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
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


class TestDeviceService(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.canonical_json = self.test_dir / "AndroidTools" / "devices.json"
        self.legacy_json = self.test_dir / "config" / "devices.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

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

        mock_dev = MagicMock()
        mock_dev.getprop.return_value = "Phone"
        mock_dev.shell.return_value = "level: 50"
        mock_client.device.return_value = mock_dev
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


if __name__ == "__main__":
    unittest.main()
