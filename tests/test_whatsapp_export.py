"""Pruebas unitarias para la detección robusta del menú Compartir de WhatsApp y receiver.

Cubre:
1. Receptor visible directamente, dentro de "Más" y tras desplazamientos.
2. Receptor instalado y ausente.
3. Instalación automática (exitosa, bloqueada/fallida, y caché de reintentos).
4. Fallback con administradores de archivos conocidos (Mis archivos, Files by Google, etc.) y etiquetas en inglés.
5. Selector sin destinos compatibles ("No se encontró AndroidTools Receiver ni una aplicación de archivos compatible").
6. Conflicto por múltiples coincidencias exactas de "AndroidTools Receiver".
7. Límites de tiempo y desplazamientos (máximo 6 desplazamientos, sin bucles infinitos).
8. Regreso a la lista principal de WhatsApp tras fallo o timeout.
9. Detección, descarga y limpieza de archivos .txt y .zip.
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import MagicMock, patch

from ui_automation.whatsapp.export import (
    KNOWN_FILE_MANAGERS,
    ShareSheetResult,
    WhatsAppExporter,
)
from ui_automation.whatsapp.receiver import (
    clear_failed_install_cache,
    ensure_receiver_ready,
)


class MockUiElement:
    """Mock completo de un UiObject de uiautomator2 con soporte de propiedades e indexación."""

    def __init__(
        self,
        text: str = "",
        content_desc: str = "",
        resource_id: str = "",
        exists: bool = True,
        bounds: dict | None = None,
    ):
        self._text = text
        self._content_desc = content_desc
        self._resource_id = resource_id
        self._exists = exists
        self._bounds = bounds or {"top": 100, "left": 100, "bottom": 200, "right": 200}
        self.clicked = False
        self.scroll = MagicMock()

    @property
    def exists(self) -> bool:
        return self._exists

    @property
    def count(self) -> int:
        return 1 if self._exists else 0

    @property
    def info(self) -> dict:
        return {
            "text": self._text,
            "contentDescription": self._content_desc,
            "resourceName": self._resource_id,
            "bounds": self._bounds,
        }

    def get_text(self) -> str:
        return self._text

    def __getitem__(self, idx: int) -> MockUiElement:
        return self

    def click(self, timeout: float = 0.0) -> bool:
        self.clicked = True
        return True


class MockDevice:
    """Mock determinista de u2.Device que devuelve MockUiElement(exists=False) por defecto."""

    def __init__(self):
        self.press = MagicMock()
        self.swipe = MagicMock()
        self.click = MagicMock()
        self.window_size = MagicMock(return_value=(1080, 2400))
        self.app_start = MagicMock()
        self.query_handler = None

    def __call__(self, **kwargs):
        if self.query_handler:
            res = self.query_handler(**kwargs)
            if res is not None:
                return res
        return MockUiElement(exists=False)


class TestWhatsAppReceiver(unittest.TestCase):
    """Pruebas de verificación e instalación del auxiliar AndroidTools Receiver."""

    def setUp(self):
        clear_failed_install_cache()

    def tearDown(self):
        clear_failed_install_cache()

    @patch("ui_automation.whatsapp.receiver.is_receiver_installed")
    def test_ensure_receiver_ready_already_installed(self, mock_installed):
        mock_installed.return_value = True
        ready, msg = ensure_receiver_ready("device-123")
        self.assertTrue(ready)
        self.assertEqual(msg, "instalado")

    @patch("pathlib.Path.is_file")
    @patch("ui_automation.whatsapp.receiver.install_receiver")
    @patch("ui_automation.whatsapp.receiver.is_receiver_installed")
    def test_ensure_receiver_ready_installs_successfully(self, mock_installed, mock_install, mock_is_file):
        mock_installed.side_effect = [False, True]
        mock_is_file.return_value = True

        ready, msg = ensure_receiver_ready("device-123")
        self.assertTrue(ready)
        self.assertEqual(msg, "instalado automáticamente")
        mock_install.assert_called_once_with(serial="device-123", timeout=25.0)

    @patch("pathlib.Path.is_file")
    @patch("ui_automation.whatsapp.receiver.install_receiver")
    @patch("ui_automation.whatsapp.receiver.is_receiver_installed")
    def test_ensure_receiver_ready_fails_and_caches_without_loop(self, mock_installed, mock_install, mock_is_file):
        mock_installed.return_value = False
        mock_is_file.return_value = True
        mock_install.side_effect = RuntimeError("INSTALL_FAILED_USER_RESTRICTED (requiere huella/contraseña)")

        # Primer intento: falla y guarda en caché
        ready1, msg1 = ensure_receiver_ready("device-123")
        self.assertFalse(ready1)
        self.assertIn("INSTALL_FAILED_USER_RESTRICTED", msg1)

        # Segundo intento consecutivo: no repite la instalación por ADB en bucle
        mock_install.reset_mock()
        ready2, msg2 = ensure_receiver_ready("device-123")
        self.assertFalse(ready2)
        self.assertIn("INSTALL_FAILED_USER_RESTRICTED", msg2)
        mock_install.assert_not_called()


class TestWhatsAppExporterShareSheet(unittest.TestCase):
    """Pruebas del controlador por etapas del menú Compartir."""

    def setUp(self):
        self.mock_device = MockDevice()
        self.exporter = WhatsAppExporter(self.mock_device)

    def test_detect_share_sheet_found_by_text(self):
        elem = MockUiElement(text="Compartir", exists=True)

        def query(**kwargs):
            if "textMatches" in kwargs and re.search(kwargs["textMatches"], "Compartir"):
                return elem
            return None

        self.mock_device.query_handler = query
        detected = self.exporter.detect_share_sheet(timeout=0.5)
        self.assertTrue(detected)

    def test_detect_share_sheet_timeout(self):
        detected = self.exporter.detect_share_sheet(timeout=0.1)
        self.assertFalse(detected)

    def test_find_receiver_exact_matches_single(self):
        target = MockUiElement(text="AndroidTools Receiver", exists=True)

        def query(**kwargs):
            if kwargs.get("text") == "AndroidTools Receiver":
                return target
            return None

        self.mock_device.query_handler = query
        matched, count = self.exporter.find_receiver_exact_matches()
        self.assertEqual(count, 1)
        self.assertEqual(matched, target)

    def test_find_receiver_exact_matches_conflict(self):
        target1 = MockUiElement(
            text="AndroidTools Receiver",
            exists=True,
            bounds={"top": 0, "left": 0, "bottom": 50, "right": 50},
        )
        target2 = MockUiElement(
            content_desc="AndroidTools Receiver",
            exists=True,
            bounds={"top": 100, "left": 100, "bottom": 150, "right": 150},
        )

        def query(**kwargs):
            if kwargs.get("text") == "AndroidTools Receiver":
                return target1
            if kwargs.get("description") == "AndroidTools Receiver":
                return target2
            return None

        self.mock_device.query_handler = query
        matched, count = self.exporter.find_receiver_exact_matches()
        self.assertEqual(count, 2)
        self.assertIsNone(matched)  # Conflicto: no elige al azar

    def test_open_more_targets(self):
        more_btn = MockUiElement(text="Más", exists=True)

        def query(**kwargs):
            if "textMatches" in kwargs and re.search(kwargs["textMatches"], "Más"):
                return more_btn
            return None

        self.mock_device.query_handler = query
        opened = self.exporter.open_more_targets()
        self.assertTrue(opened)
        self.assertTrue(more_btn.clicked)

    def test_swipe_share_sheet_accessible(self):
        scrollable = MockUiElement(exists=True)

        def query(**kwargs):
            if kwargs.get("scrollable"):
                return scrollable
            return None

        self.mock_device.query_handler = query
        swiped = self.exporter.swipe_share_sheet()
        self.assertTrue(swiped)
        scrollable.scroll.horiz.forward.assert_called_once_with(steps=40)

    def test_swipe_share_sheet_coordinates_fallback(self):
        # Sin scrollable disponible: debe recurrir a coordenadas
        self.mock_device.query_handler = None
        swiped_coords = self.exporter.swipe_share_sheet()
        self.assertTrue(swiped_coords)
        self.mock_device.swipe.assert_called_once()

    def test_swipe_share_sheet_vertical_uses_vertical_scroll(self):
        scrollable = MockUiElement(exists=True)

        def query(**kwargs):
            if kwargs.get("scrollable"):
                return scrollable
            return None

        self.mock_device.query_handler = query
        swiped = self.exporter.swipe_share_sheet(direction="vertical")
        self.assertTrue(swiped)
        scrollable.scroll.vert.forward.assert_called_once_with(steps=40)
        scrollable.scroll.horiz.forward.assert_not_called()

    def test_find_file_manager_target_spanish_and_english(self):
        # 1. Probar "Mis archivos"
        my_files = MockUiElement(text="Mis archivos", exists=True)

        def query_es(**kwargs):
            if "textMatches" in kwargs and re.search(kwargs["textMatches"], "Mis archivos"):
                return my_files
            return None

        self.mock_device.query_handler = query_es
        target, name = self.exporter.find_file_manager_target()
        self.assertIsNotNone(target)
        self.assertEqual(name, "Mis archivos")

        # 2. Probar "Files by Google"
        files_google = MockUiElement(text="Files by Google", exists=True)

        def query_en(**kwargs):
            if "textMatches" in kwargs and re.search(kwargs["textMatches"], "Files by Google"):
                return files_google
            return None

        self.mock_device.query_handler = query_en
        target2, name2 = self.exporter.find_file_manager_target()
        self.assertIsNotNone(target2)
        self.assertEqual(name2, "Files by Google")

    def test_find_file_manager_target_recognizes_requested_names(self):
        for manager_name in (
            "EX Administrador de Archivos",
            "Gestor de archivos",
            "Cx Administrador de Archivos",
        ):
            with self.subTest(manager_name=manager_name):
                manager = MockUiElement(text=manager_name, exists=True)

                def query(**kwargs):
                    if "textMatches" in kwargs and re.search(kwargs["textMatches"], manager_name):
                        return manager
                    return None

                self.mock_device.query_handler = query
                target, name = self.exporter.find_file_manager_target()
                self.assertIs(target, manager)
                self.assertEqual(name, manager_name)

    def test_find_file_manager_no_match(self):
        self.mock_device.query_handler = None
        target, name = self.exporter.find_file_manager_target()
        self.assertIsNone(target)
        self.assertIsNone(name)

    def test_confirm_file_save_clicks_save_button(self):
        save_btn = MockUiElement(text="Guardar", exists=True)

        def query(**kwargs):
            if "textMatches" in kwargs and re.search(kwargs["textMatches"], "Guardar"):
                return save_btn
            return None

        self.mock_device.query_handler = query
        confirmed = self.exporter.confirm_file_save(timeout=0.5)
        self.assertTrue(confirmed)
        self.assertTrue(save_btn.clicked)

    def test_recover_to_whatsapp_chat_list(self):
        chats_header = MockUiElement(text="Chats", exists=True)
        state = {"in_whatsapp": False}

        def query(**kwargs):
            if kwargs.get("text") == "Chats" and state["in_whatsapp"]:
                return chats_header
            return None

        def mock_press(key):
            if key == "back":
                state["in_whatsapp"] = True

        self.mock_device.query_handler = query
        self.mock_device.press.side_effect = mock_press

        recovered = self.exporter.recover_to_whatsapp_chat_list(max_attempts=3)
        self.assertTrue(recovered)
        self.mock_device.press.assert_called_with("back")


class TestWhatsAppExporterStructuredFlow(unittest.TestCase):
    """Pruebas del controlador handle_share_sheet_flow y export_chat_structured."""

    def setUp(self):
        self.mock_device = MockDevice()
        self.exporter = WhatsAppExporter(self.mock_device)

    @patch.object(WhatsAppExporter, "detect_share_sheet", return_value=False)
    def test_share_sheet_timeout(self, mock_detect):
        res = self.exporter.handle_share_sheet_flow("Familia", 1000, prefer_zip=False)
        self.assertFalse(res.success)
        self.assertEqual(res.stage_failed, "wait_share_sheet")
        self.assertIn("20 segundos", res.failure_reason)

    @patch.object(WhatsAppExporter, "detect_share_sheet", return_value=True)
    @patch.object(WhatsAppExporter, "_wait_for_receiver")
    def test_share_sheet_receiver_conflict(self, mock_wait_rec, mock_detect):
        mock_wait_rec.return_value = (None, 2)

        res = self.exporter.handle_share_sheet_flow("Familia", 1000, prefer_zip=False)
        self.assertFalse(res.success)
        self.assertEqual(res.stage_failed, "receiver_conflict")
        self.assertIn("Conflicto", res.failure_reason)

    @patch.object(WhatsAppExporter, "detect_share_sheet", return_value=True)
    @patch.object(WhatsAppExporter, "_wait_for_receiver")
    @patch.object(WhatsAppExporter, "_wait_for_receiver_receipt")
    def test_share_sheet_receiver_direct_success(self, mock_wait_receipt, mock_wait_rec, mock_detect):
        rec_elem = MockUiElement(text="AndroidTools Receiver", exists=True)
        mock_wait_rec.return_value = (rec_elem, 1)

        def sim_receipt(snapshot, started_at, zip_p, result):
            result.success = True
            result.detected_path = "/data/user/0/com.androidtools.receiver/files/WhatsApp_Familia.txt"
            return result

        mock_wait_receipt.side_effect = sim_receipt

        res = self.exporter.handle_share_sheet_flow("Familia", 1000, prefer_zip=False)
        self.assertTrue(res.success)
        self.assertEqual(res.method, "receiver")
        self.assertEqual(res.target_used, "AndroidTools Receiver")
        self.assertTrue(rec_elem.clicked)
        self.assertIn("com.androidtools.receiver", res.detected_path)

    @patch.object(WhatsAppExporter, "detect_share_sheet", return_value=True)
    @patch.object(WhatsAppExporter, "_wait_for_receiver", return_value=(None, 0))
    @patch.object(WhatsAppExporter, "open_more_targets", return_value=False)
    @patch.object(WhatsAppExporter, "swipe_share_sheet", return_value=True)
    @patch.object(WhatsAppExporter, "find_file_manager_target", return_value=(None, None))
    def test_share_sheet_max_swipes_and_no_target_fallback_error(
        self, mock_find_fm, mock_swipe, mock_more, mock_wait_rec, mock_detect
    ):
        res = self.exporter.handle_share_sheet_flow("Familia", 1000, prefer_zip=False)
        self.assertFalse(res.success)
        self.assertEqual(res.stage_failed, "no_compatible_target")
        self.assertEqual(res.scrolls_count, 6)
        self.assertIn("No se encontró AndroidTools Receiver ni una aplicación de archivos compatible", res.failure_reason)

    @patch.object(WhatsAppExporter, "detect_share_sheet", return_value=True)
    @patch.object(WhatsAppExporter, "_wait_for_receiver", return_value=(None, 0))
    @patch.object(WhatsAppExporter, "open_more_targets", return_value=True)
    @patch.object(WhatsAppExporter, "find_receiver_exact_matches", return_value=(None, 0))
    @patch.object(WhatsAppExporter, "swipe_share_sheet", return_value=False)
    @patch.object(WhatsAppExporter, "find_file_manager_target")
    @patch.object(WhatsAppExporter, "confirm_file_save", return_value=True)
    @patch.object(WhatsAppExporter, "_wait_and_detect_file")
    def test_share_sheet_file_manager_fallback_success(
        self,
        mock_wait_file,
        mock_confirm,
        mock_find_fm,
        mock_swipe,
        mock_find_matches,
        mock_more,
        mock_wait_rec,
        mock_detect,
    ):
        fm_elem = MockUiElement(text="Mis archivos", exists=True)
        mock_find_fm.return_value = (fm_elem, "Mis archivos")

        def sim_file(chat, ts, zip_p, result):
            result.success = True
            result.detected_path = "/sdcard/Download/WhatsApp_Familia.zip"
            return result

        mock_wait_file.side_effect = sim_file

        res = self.exporter.handle_share_sheet_flow("Familia", 1000, prefer_zip=True)
        self.assertTrue(res.success)
        self.assertEqual(res.method, "file_manager")
        self.assertEqual(res.target_used, "Mis archivos")
        self.assertTrue(fm_elem.clicked)
        mock_confirm.assert_called_once()
        self.assertEqual(res.detected_path, "/sdcard/Download/WhatsApp_Familia.zip")

    @patch.object(WhatsAppExporter, "open_chat_by_name", return_value=True)
    @patch.object(WhatsAppExporter, "recover_to_whatsapp_chat_list")
    def test_export_chat_structured_recovers_on_failure(self, mock_recover, mock_open):
        # Menú de opciones (3 puntos) no encontrado -> debe fallar y recuperar
        remote_path, res = self.exporter.export_chat_structured("Familia")
        self.assertIsNone(remote_path)
        self.assertFalse(res.success)
        self.assertEqual(res.stage_failed, "menu_export_option")
        mock_recover.assert_called_once()


if __name__ == "__main__":
    unittest.main()
