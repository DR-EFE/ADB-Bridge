from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.services.wireless_qr_service import WirelessQrError, WirelessQrPairingService, _successful_pair


class TestWirelessQrService(unittest.TestCase):
    @unittest.skipIf(__import__("importlib").util.find_spec("qrcode") is None, "qrcode no instalado")
    def test_payload_has_android_qr_format_and_image(self):
        payload = WirelessQrPairingService(adb_path="adb").create_payload()
        self.assertTrue(payload.value.startswith("WIFI:T:ADB;S:"))
        self.assertIn(f"S:{payload.service_name};", payload.value)
        self.assertIn(f"P:{payload.password};;", payload.value)
        self.assertTrue(payload.image_base64)

    def test_pair_output_validation(self):
        self.assertTrue(_successful_pair("Successfully paired to 192.168.1.2:40000"))
        self.assertTrue(_successful_pair("Pairing successful"))
        self.assertFalse(_successful_pair("failed to pair"))

    def test_timeout_is_reported_when_no_pairing_service(self):
        service = WirelessQrPairingService(adb_path="adb")
        payload = type("Payload", (), {"service_name": "missing", "password": "secret"})()
        with patch.object(service, "_ensure_mdns"), patch.object(service, "_mdns_services", return_value=[]):
            with self.assertRaises(WirelessQrError):
                service.pair(payload, timeout=0.01)


if __name__ == "__main__":
    unittest.main()
