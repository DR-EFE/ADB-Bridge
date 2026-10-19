from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.pages.whatsapp_page import select_discovered_chats, split_chat_selection
from ui_automation.whatsapp.chats import WhatsAppChatManager
from ui_automation.whatsapp.client import WhatsAppAppState, WhatsAppClient


class _Element:
    def __init__(self, text: str = "", exists: bool = True):
        self._text = text
        self.exists = exists

    def get_text(self) -> str:
        return self._text


class _Elements:
    def __init__(self, names: list[str]):
        self._items = [_Element(name) for name in names]
        self.exists = bool(self._items)
        self.count = len(self._items)

    def __getitem__(self, index: int) -> _Element:
        return self._items[index]


class _Image:
    def save(self, path: Path) -> None:
        Path(path).write_bytes(b"test-png")


class _DiscoveryDevice:
    def __init__(self):
        self.position = 0
        self.pages = [["A", "B"], ["B", "C"], ["C", "D"], ["D", "E"]]

    def __call__(self, **kwargs):
        if "resourceIdMatches" in kwargs:
            return _Elements(self.pages[self.position])
        return _Element(exists=False)

    def screenshot(self):
        return _Image()

    def swipe_ext(self, direction: str, scale: float = 0.5) -> None:
        if direction == "up":
            self.position = min(self.position + 1, len(self.pages) - 1)
        else:
            self.position = max(self.position - 1, 0)


class _DiscoveryClient:
    def __init__(self):
        self.d = _DiscoveryDevice()

    def ensure_chat_list(self) -> bool:
        return True


class _StateDevice:
    def __init__(self, foreground: str, pid: str):
        self.foreground = foreground
        self.pid = pid
        self.app_start = MagicMock()
        self.app_stop = MagicMock()
        self.press = MagicMock()

    def app_current(self):
        return {"package": self.foreground, "activity": "MainActivity"}

    def app_list(self):
        return ["com.whatsapp"]

    def shell(self, _command: str):
        return SimpleNamespace(output=self.pid)

    def __call__(self, **_kwargs):
        return _Element(exists=False)


class TestChatSelection(unittest.TestCase):
    def test_numbers_and_ranges_keep_visual_order(self):
        names = ["Uno", "Dos", "Tres", "Cuatro", "Cinco"]
        self.assertEqual(select_discovered_chats(names, "1,3-5,3"), ["Uno", "Tres", "Cuatro", "Cinco"])

    def test_out_of_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "1-2"):
            select_discovered_chats(["Uno", "Dos"], "3")

    def test_number_in_name_field_is_treated_as_discovery_index(self):
        names, number_selection = split_chat_selection("3, Miriam, 5-7")
        self.assertEqual(names, ["Miriam"])
        self.assertEqual(number_selection, "3,5-7")


class TestWhatsAppDiscovery(unittest.TestCase):
    @patch("ui_automation.whatsapp.chats.time.sleep", return_value=None)
    def test_three_scrolls_create_four_screenshots_and_manifest(self, _sleep):
        client = _DiscoveryClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = WhatsAppChatManager(client).discover_chats(temp_dir, scrolls=3)
            self.assertEqual(result["chat_names"], ["A", "B", "C", "D", "E"])
            self.assertEqual(result["scrolls"], 3)
            self.assertEqual(len(result["screenshots"]), 4)
            self.assertEqual(client.d.position, 0)
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["chats"][2], {"number": 3, "name": "C"})


class TestWhatsAppStateRestoration(unittest.TestCase):
    def test_closed_whatsapp_is_stopped_even_after_error(self):
        device = _StateDevice("com.example.notes", "")
        client = WhatsAppClient(device, auto_watchers=False)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with client.preserve_app_state():
                raise RuntimeError("boom")
        device.app_stop.assert_called_once_with("com.whatsapp")
        device.app_start.assert_called_once_with("com.example.notes", stop=False)

    def test_background_whatsapp_remains_running_and_previous_app_returns(self):
        device = _StateDevice("com.example.notes", "1234")
        client = WhatsAppClient(device, auto_watchers=False)
        with client.preserve_app_state():
            pass
        device.app_stop.assert_not_called()
        device.app_start.assert_called_once_with("com.example.notes", stop=False)

    @patch("ui_automation.whatsapp.export.WhatsAppExporter.open_chat_by_name", return_value=True)
    def test_foreground_conversation_is_reopened(self, open_chat):
        device = _StateDevice("com.whatsapp", "1234")
        client = WhatsAppClient(device, auto_watchers=False)
        client.ensure_chat_list = MagicMock(return_value=True)
        state = WhatsAppAppState(
            whatsapp_package="com.whatsapp",
            foreground_package="com.whatsapp",
            foreground_activity="Conversation",
            was_running=True,
            was_foreground=True,
            was_on_chat_list=False,
            was_in_conversation=True,
            conversation_title="Familia",
        )
        self.assertTrue(client.restore_app_state(state))
        open_chat.assert_called_once_with("Familia")


if __name__ == "__main__":
    unittest.main()
