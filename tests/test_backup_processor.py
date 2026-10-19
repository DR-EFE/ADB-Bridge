"""Pruebas unitarias para BackupProcessor y normalización de respaldos."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.services.backup_processor import (
    BackupProcessor,
    parse_apps,
    parse_bluetooth,
    parse_calendar,
    parse_calls,
    parse_clipboard,
    parse_contacts,
    parse_dictionary,
    parse_settings,
    parse_sms,
    parse_wifi,
)


class TestBackupProcessor(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_contacts(self):
        sample = (
            "Row 0: _id=1, display_name=Juan Perez, data1=+521234567890\n"
            "Row 1: _id=2, display_name=Maria Gomez, data1=+529876543210\n"
        )
        records = parse_contacts({"contacts_raw.txt": sample})
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["Nombre"], "Juan Perez")
        self.assertEqual(records[0]["Teléfono"], "+521234567890")

    def test_parse_sms(self):
        sample = (
            "Row: 0 _id=10, address=+12345, body=Tu codigo de acceso es 9876, date=1690000000000, type=1\n"
        )
        records = parse_sms({"sms_all.txt": sample})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["Remitente/Destino"], "+12345")
        self.assertIn("9876", records[0]["Mensaje"])

    def test_parse_calls(self):
        sample = (
            "Row: 0 _id=1, number=+555123, name=Soporte, duration=120, type=1, date=1690000000000\n"
        )
        records = parse_calls({"call_log.txt": sample})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["Número"], "+555123")
        self.assertEqual(records[0]["Tipo"], "Entrante")

    def test_parse_apps(self):
        files_map = {
            "apps_user.txt": "package:com.whatsapp\npackage:com.spotify.music\n",
            "apps_system.txt": "package:com.android.settings\n",
        }
        records = parse_apps(files_map)
        pkgs = [r["Paquete"] for r in records]
        self.assertIn("com.whatsapp", pkgs)
        self.assertIn("com.android.settings", pkgs)

    def test_parse_wifi_and_bluetooth(self):
        wifi_sample = (
            "Network Id   SSID   Security type\n"
            "1            MiCasa_WiFi   WPA2_PSK\n"
        )
        wifi_records = parse_wifi({"wifi_networks.txt": wifi_sample})
        self.assertTrue(any(r["SSID"] == "MiCasa_WiFi" for r in wifi_records))

        bt_sample = "00:11:22:33:44:55 [Auriculares BT]\n"
        bt_records = parse_bluetooth({"bluetooth_devices.txt": bt_sample})
        self.assertTrue(any(r["MAC / Dirección"] == "00:11:22:33:44:55" for r in bt_records))

    def test_process_full_backup_generates_json_and_xlsx(self):
        # Crear estructura de backup de prueba
        contacts_dir = self.test_dir / "Contacts"
        contacts_dir.mkdir(parents=True)
        (contacts_dir / "contacts_raw.txt").write_text("Row 0: _id=1, name=Carlos, data1=555111\n", encoding="utf-8")

        sms_dir = self.test_dir / "SMS"
        sms_dir.mkdir(parents=True)
        (sms_dir / "sms_all.txt").write_text("Row 0: _id=1, address=111, body=Hola, type=1\n", encoding="utf-8")

        manifest_data = {"OverallStatus": "PARTIAL", "Complete": False}
        (self.test_dir / "MANIFEST.json").write_text(json.dumps(manifest_data), encoding="utf-8")

        processor = BackupProcessor(self.test_dir)
        res = processor.process()

        self.assertTrue(Path(res.json_path).exists())
        self.assertTrue(Path(res.xlsx_path).exists())
        self.assertEqual(res.overall_status, "PARTIAL")
        self.assertIn("contacts", res.categories)
        self.assertEqual(res.categories["contacts"].total_records, 1)

    def test_fallback_raw_preservation_on_permission_denied(self):
        cal_dir = self.test_dir / "Calendar"
        cal_dir.mkdir(parents=True)
        (cal_dir / "calendar_events.txt").write_text("[PERMISO DENEGADO]\nSecurityException: Permission Denial", encoding="utf-8")

        processor = BackupProcessor(self.test_dir)
        res = processor.process()

        cal_cat = res.categories["calendar"]
        self.assertEqual(cal_cat.status, "DENIED")
        self.assertEqual(cal_cat.denied_count, 1)
        self.assertIsNotNone(cal_cat.raw_fallback)
        self.assertIn("SecurityException", cal_cat.raw_fallback)


if __name__ == "__main__":
    unittest.main()
