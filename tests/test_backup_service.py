"""Pruebas unitarias para BackupService y generación de manifiestos."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.services.backup_service import (
    BackupProgress,
    BackupService,
    CategoryDef,
    QueryDef,
    get_available_category_definitions,
    get_default_backup_dir,
)


class TestBackupService(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.service = BackupService()

    def tearDown(self):
        self.service.cancel()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_category_definitions(self):
        """Verifica que existan todas las categorías requeridas."""
        cats = get_available_category_definitions()
        cat_ids = [c.id for c in cats]
        required = [
            "multimedia",
            "contacts",
            "sms",
            "calls",
            "calendar",
            "settings",
            "apps",
            "wifi",
            "bluetooth",
            "dictionary",
            "clipboard",
        ]
        for req in required:
            self.assertIn(req, cat_ids)

        multimedia_cat = next(c for c in cats if c.id == "multimedia")
        self.assertTrue(multimedia_cat.is_multimedia)
        for folder in ["DCIM", "Download", "Documents", "Music", "Pictures"]:
            self.assertIn(folder, multimedia_cat.folders)

    def test_run_backup_generates_manifests_and_summary(self):
        """Verifica que el respaldo genere MANIFEST.json y RESUMEN_BACKUP.json con estados."""
        def fake_run_cmd(adb_exe, args, timeout=None):
            # args es ['-s', serial, 'shell', ...]
            cmd_str = " ".join(args)
            if "contacts" in cmd_str:
                return 0, "Row 0: name=John Doe, phone=+123456\n", ""
            if "sms" in cmd_str:
                return 0, "", ""  # vacío => EMPTY
            if "cal" in cmd_str:
                return 1, "", "java.lang.SecurityException: Permission Denial"  # DENIED
            return 0, "dummy data", ""

        custom_cats = [
            CategoryDef(
                id="contacts",
                name="Contactos",
                description="test",
                sub_folder="Contacts",
                queries=[QueryDef("contacts_raw", "contacts.txt", "content query --uri test_contacts")],
            ),
            CategoryDef(
                id="sms",
                name="SMS",
                description="test",
                sub_folder="SMS",
                queries=[QueryDef("sms_all", "sms.txt", "content query --uri test_sms")],
            ),
            CategoryDef(
                id="calendar",
                name="Calendario",
                description="test",
                sub_folder="Calendar",
                queries=[QueryDef("cal_all", "cal.txt", "content query --uri test_cal")],
            ),
        ]

        with patch.object(self.service, "_run_cmd", side_effect=fake_run_cmd):
            summary = self.service.run_backup(
                serial="TEST_SERIAL",
                device_label="SM-A556E",
                destination_dir=self.test_dir,
                categories=custom_cats,
            )

        manifest_file = self.test_dir / "MANIFEST.json"
        summary_file = self.test_dir / "RESUMEN_BACKUP.json"

        self.assertTrue(manifest_file.is_file())
        self.assertTrue(summary_file.is_file())

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest["DeviceSerial"], "TEST_SERIAL")
        self.assertEqual(manifest["DeviceLabel"], "SM-A556E")
        self.assertEqual(len(manifest["Categories"]), 3)

        # Contactos debe ser OK
        contacts_res = next(c for c in manifest["Categories"] if c["Id"] == "contacts")
        self.assertEqual(contacts_res["Status"], "OK")

        # SMS debe ser EMPTY
        sms_res = next(c for c in manifest["Categories"] if c["Id"] == "sms")
        self.assertEqual(sms_res["Status"], "EMPTY")

        # Calendario debe ser DENIED
        cal_res = next(c for c in manifest["Categories"] if c["Id"] == "calendar")
        self.assertEqual(cal_res["Status"], "DENIED")

        # Al haber una categoría OK y otras EMPTY/DENIED, el estado global debe ser PARTIAL
        self.assertEqual(manifest["OverallStatus"], "PARTIAL")
        self.assertFalse(manifest["Complete"])

    def test_cancellation(self):
        """Verifica que la cancelación interrumpa y reporte CANCELLED/PARTIAL."""
        self.service._cancel_event.set()
        custom_cats = [
            CategoryDef(
                id="contacts",
                name="Contactos",
                description="test",
                sub_folder="Contacts",
                queries=[QueryDef("c1", "c1.txt", "cmd")],
            )
        ]
        summary = self.service.run_backup(
            serial="TEST_SERIAL",
            device_label="SM-A556E",
            destination_dir=self.test_dir,
            categories=custom_cats,
            reset_cancel=False,
        )
        self.assertEqual(summary["OverallStatus"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
