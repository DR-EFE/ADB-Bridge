"""Pruebas unitarias para la separación y persistencia de notificaciones."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui_automation.monitor import NotificationMonitor


class TestNotificationSeparation(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.notif_dir = self.test_dir / "notifications"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_storage_in_notifications_folder(self):
        monitor = NotificationMonitor(logs_dir=str(self.notif_dir))
        self.assertEqual(monitor.logs_dir, self.notif_dir)
        self.assertTrue(self.notif_dir.exists())
        self.assertEqual(monitor.important_log_path, self.notif_dir / "notificaciones_importantes.json")

    def test_migration_from_legacy_logs_folder(self):
        legacy_dir = Path("logs")
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_imp = legacy_dir / "notificaciones_importantes.json"

        # Simular archivo existente previo
        sample_data = [{"tipo": "OTP", "codigo": "123456", "detected_at": "2026-10-18T10:00:00"}]
        legacy_imp.write_text(json.dumps(sample_data), encoding="utf-8")

        # Iniciar monitor apuntando a nueva carpeta
        monitor = NotificationMonitor(logs_dir=str(self.notif_dir))

        # Verificar que se copió/migró a notifications/
        migrated_imp = self.notif_dir / "notificaciones_importantes.json"
        self.assertTrue(migrated_imp.exists())
        content = json.loads(migrated_imp.read_text(encoding="utf-8"))
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["codigo"], "123456")


if __name__ == "__main__":
    unittest.main()
