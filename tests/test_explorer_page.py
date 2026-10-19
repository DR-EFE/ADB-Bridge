"""Pruebas de formato y robustez para el explorador remoto."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.pages.explorer_page import ExplorerPage


class TestExplorerPageHelpers(unittest.TestCase):
    def test_parse_listing_preserves_spaces_and_sorts_folders_first(self):
        output = "\n".join(
            [
                "total 12",
                "-rw-rw---- 1 1023 1023 1536 2026-10-18 12:00 niño.txt",
                "drwxrwx--- 2 1023 1023 4096 2026-10-18 12:00 Mi carpeta",
                "lrwxrwxrwx 1 1023 1023 12 2026-10-18 12:00 enlace -> /sdcard/Download",
            ]
        )
        entries = ExplorerPage._parse_ls_output(output)
        self.assertEqual(["Mi carpeta", "enlace", "niño.txt"], [entry["name"] for entry in entries])
        self.assertEqual("1536", entries[-1]["size"])

    def test_storage_summary_and_actionable_errors(self):
        summary = ExplorerPage._format_storage(
            "Filesystem 1K-blocks Used Available Use% Mounted on\n"
            "/dev/fuse 10000 2500 7500 25% /storage/emulated"
        )
        self.assertIn("7.3 MB libres", summary)
        self.assertIn("9.8 MB", summary)
        self.assertIn(
            "Espera la reconexión Wi‑Fi",
            ExplorerPage._friendly_remote_error("error: device offline", "/sdcard", "192.168.1.5:5555"),
        )
        self.assertIn(
            "no permite abrir",
            ExplorerPage._friendly_remote_error("Permission denied", "/data", "device"),
        )

    def test_remote_name_validation(self):
        self.assertIsNone(ExplorerPage._validate_remote_name("Fotos de viaje"))
        self.assertIsNotNone(ExplorerPage._validate_remote_name("../privado"))
        self.assertIsNotNone(ExplorerPage._validate_remote_name("."))

