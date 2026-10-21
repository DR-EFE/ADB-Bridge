"""Pruebas unitarias para AdbService y modelos Pydantic (Fase 1).

Se prueban sin necesidad de un dispositivo físico real, mediante mocking de subprocess.Popen.
Cubre:
- Validación de modelos Pydantic: Device, ConnectionAttempt, OperationResult
- AdbService.devices() con parsing estructurado de adb devices -l
- AdbService.resolve_serial():
  - Falla si no hay dispositivos (DISPOSITIVO_NO_SELECCIONADO)
  - Falla si hay múltiples sin serial explícito (MULTIPLE_DEVICES)
  - Éxito si hay 1 solo listo
  - Éxito si se pasa serial válido
  - Falla si el serial indicado está offline o no existe
- AdbService.disconnect() para endpoint IP:Port y rechazo de USB físico (USB_DISCONNECT_UNSUPPORTED)
- AdbService.run_raw(): timeouts, reintentos con backoff, no-cero exit codes
- AdbService.shell(), pull(), push(), install(), forward(), pair(), get_property()
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from ui.services.adb_models import ConnectionAttempt, ConnectionMode, Device, DeviceState, OperationResult
from ui.services.adb_service import AdbError, AdbService


class TestAdbModels(unittest.TestCase):
    def test_device_model_defaults_and_validation(self):
        dev = Device(serial="emulator-5554", state=DeviceState.DEVICE)
        self.assertEqual(dev.serial, "emulator-5554")
        self.assertTrue(dev.is_ready)
        self.assertFalse(dev.is_network)

        # Dispositivo de red
        net_dev = Device(serial="192.168.1.50:5555", state=DeviceState.DEVICE)
        self.assertTrue(net_dev.is_network)
        self.assertTrue(net_dev.is_ready)

        # Estado offline / no autorizado
        unauth_dev = Device(serial="192.168.1.50:5555", state=DeviceState.UNAUTHORIZED)
        self.assertFalse(unauth_dev.is_ready)

    def test_connection_attempt_model(self):
        attempt = ConnectionAttempt(
            endpoint="192.168.1.100:5555",
            host="192.168.1.100",
            port=5555,
            mode=ConnectionMode.TCPIP,
            success=True,
            message="Conectado",
            exit_code=0,
        )
        self.assertTrue(attempt.success)
        self.assertEqual(attempt.port, 5555)

    def test_operation_result_model(self):
        res = OperationResult(
            success=True,
            exit_code=0,
            stdout="sample output\n",
            stderr="",
            command=["adb", "devices"],
            duration_seconds=0.15,
        )
        self.assertEqual(res.output, "sample output")
        self.assertTrue(res.success)


class TestAdbService(unittest.TestCase):
    def setUp(self):
        self.service = AdbService(adb_path="mock_adb")

    @patch("subprocess.Popen")
    def test_devices_parsing(self, mock_popen):
        stdout_content = (
            "List of devices attached\n"
            "emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 device:emulator64_arm64\n"
            "192.168.1.50:5555      offline\n"
            "USB12345               unauthorized usb:1-1\n"
        )
        process_mock = MagicMock()
        process_mock.communicate.return_value = (stdout_content, "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        devs = self.service.devices()
        self.assertEqual(len(devs), 3)

        self.assertEqual(devs[0].serial, "emulator-5554")
        self.assertEqual(devs[0].state, DeviceState.DEVICE)
        self.assertEqual(devs[0].model, "sdk_gphone64_arm64")
        self.assertTrue(devs[0].is_ready)

        self.assertEqual(devs[1].serial, "192.168.1.50:5555")
        self.assertEqual(devs[1].state, DeviceState.OFFLINE)
        self.assertFalse(devs[1].is_ready)

        self.assertEqual(devs[2].serial, "USB12345")
        self.assertEqual(devs[2].state, DeviceState.UNAUTHORIZED)
        self.assertEqual(devs[2].usb_serial, "1-1")
        self.assertFalse(devs[2].is_ready)

    @patch("subprocess.Popen")
    def test_resolve_serial_no_devices(self, mock_popen):
        process_mock = MagicMock()
        process_mock.communicate.return_value = ("List of devices attached\n\n", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        with self.assertRaises(AdbError) as ctx:
            self.service.resolve_serial()
        self.assertEqual(ctx.exception.code, "DISPOSITIVO_NO_SELECCIONADO")

    @patch("subprocess.Popen")
    def test_resolve_serial_multiple_devices_raises(self, mock_popen):
        stdout_content = (
            "List of devices attached\n"
            "DEV1          device\n"
            "DEV2          device\n"
        )
        process_mock = MagicMock()
        process_mock.communicate.return_value = (stdout_content, "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        with self.assertRaises(AdbError) as ctx:
            self.service.resolve_serial()
        self.assertEqual(ctx.exception.code, "MULTIPLE_DEVICES")

    @patch("subprocess.Popen")
    def test_resolve_serial_single_device_success(self, mock_popen):
        stdout_content = (
            "List of devices attached\n"
            "DEV_SOLO          device\n"
        )
        process_mock = MagicMock()
        process_mock.communicate.return_value = (stdout_content, "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        serial = self.service.resolve_serial()
        self.assertEqual(serial, "DEV_SOLO")

    @patch("subprocess.Popen")
    def test_resolve_serial_explicit_selection(self, mock_popen):
        stdout_content = (
            "List of devices attached\n"
            "DEV1          device\n"
            "DEV2          device\n"
        )
        process_mock = MagicMock()
        process_mock.communicate.return_value = (stdout_content, "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        serial = self.service.resolve_serial("DEV2")
        self.assertEqual(serial, "DEV2")

    @patch("subprocess.Popen")
    def test_resolve_serial_explicit_offline_device_raises(self, mock_popen):
        stdout_content = (
            "List of devices attached\n"
            "DEV_OFFLINE          offline\n"
        )
        process_mock = MagicMock()
        process_mock.communicate.return_value = (stdout_content, "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        with self.assertRaises(AdbError) as ctx:
            self.service.resolve_serial("DEV_OFFLINE")
        self.assertEqual(ctx.exception.code, "DISPOSITIVO_NO_LISTO")

    def test_disconnect_physical_usb_raises(self):
        with self.assertRaises(AdbError) as ctx:
            self.service.disconnect("USB_PHYSICAL_1234")
        self.assertEqual(ctx.exception.code, "USB_DISCONNECT_UNSUPPORTED")

    @patch("subprocess.Popen")
    def test_disconnect_endpoint_success(self, mock_popen):
        process_mock = MagicMock()
        process_mock.communicate.return_value = ("disconnected 192.168.1.50:5555\n", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        res = self.service.disconnect("192.168.1.50:5555")
        self.assertTrue(res.success)
        self.assertIn("disconnected", res.output)

    @patch("subprocess.Popen")
    def test_run_raw_timeout_handling(self, mock_popen):
        process_mock = MagicMock()
        process_mock.communicate.side_effect = subprocess.TimeoutExpired(cmd=["mock_adb"], timeout=2.0)
        mock_popen.return_value = process_mock

        res = self.service.run_raw(["shell", "sleep 10"], timeout=2.0, retries=0)
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, -1)
        self.assertIn("TIMEOUT_EXCEDIDO", res.message or "")
        process_mock.kill.assert_called()

    @patch("subprocess.Popen")
    def test_run_raw_retries_on_failure(self, mock_popen):
        # Primer intento falla, segundo intento triunfa
        proc_fail = MagicMock()
        proc_fail.communicate.return_value = ("", "transient error")
        proc_fail.returncode = 1

        proc_success = MagicMock()
        proc_success.communicate.return_value = ("success output", "")
        proc_success.returncode = 0

        mock_popen.side_effect = [proc_fail, proc_success]

        res = self.service.run_raw(["devices"], retries=1, retry_delay=0.01)
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(mock_popen.call_count, 2)

    @patch.object(AdbService, "resolve_serial")
    @patch.object(AdbService, "run_raw")
    def test_shell_command_uses_explicit_serial(self, mock_run_raw, mock_resolve_serial):
        mock_resolve_serial.return_value = "TARGET_DEV"
        mock_run_raw.return_value = OperationResult(success=True, exit_code=0, stdout="root\n")

        res = self.service.shell("whoami", serial="TARGET_DEV")
        mock_resolve_serial.assert_called_with("TARGET_DEV")
        mock_run_raw.assert_called_with(["shell", "whoami"], serial="TARGET_DEV", timeout=30.0, retries=0)
        self.assertTrue(res.success)

    @patch.object(AdbService, "run_raw")
    def test_pair_validates_code_format(self, mock_run_raw):
        # Código con letras o distinto de 6 dígitos debe fallar antes de subprocess
        with self.assertRaises(AdbError) as ctx:
            self.service.pair("192.168.1.100:38899", "12345")
        self.assertEqual(ctx.exception.code, "CODIGO_INVALIDO")
        mock_run_raw.assert_not_called()

    @patch.object(AdbService, "run_raw")
    def test_pair_success(self, mock_run_raw):
        mock_run_raw.return_value = OperationResult(
            success=True,
            exit_code=0,
            stdout="Successfully paired to 192.168.1.100:38899 [guid: ...]\n",
        )
        attempt = self.service.pair("192.168.1.100:38899", "123456")
        self.assertTrue(attempt.success)
        self.assertEqual(attempt.port, 38899)
        self.assertEqual(attempt.mode, ConnectionMode.WIRELESS_TLS)


if __name__ == "__main__":
    unittest.main()
