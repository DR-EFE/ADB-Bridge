from types import SimpleNamespace

import pytest

from ui.controllers.integration_controller import IntegrationController, UiActionError


class FakeAdb:
    def __init__(self): self.serials = []
    def resolve_serial(self, serial): self.serials.append(serial); return serial


def test_controller_requires_explicit_device_selection():
    controller = IntegrationController(SimpleNamespace(serial=None), object(), adb=FakeAdb(), backup=object(), sync=object(), health=object())
    with pytest.raises(UiActionError, match="Selecciona explícitamente"):
        controller.selected_serial()


def test_controller_passes_selected_serial_to_backup_queue():
    adb, calls = FakeAdb(), []
    backup = SimpleNamespace(enqueue_profile=lambda *args, **kwargs: calls.append((args, kwargs)) or "job-1")
    controller = IntegrationController(SimpleNamespace(serial="USB-42"), "queue", adb=adb, backup=backup, sync=object(), health=object())
    assert controller.enqueue_backup("photos_videos", "backups") == "job-1"
    assert adb.serials == ["USB-42"]
    assert calls[0][1]["serial"] == "USB-42"
