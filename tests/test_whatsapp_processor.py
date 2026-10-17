"""Pruebas del procesamiento local de exportaciones de WhatsApp."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from ui_automation.whatsapp.processor import (
    ChatExporter,
    ChatFilter,
    WhatsAppParser,
    process_chat_file,
)


class TestWhatsAppParser(unittest.TestCase):
    def test_parses_spanish_english_dates_and_multiline_messages(self) -> None:
        text = "12/05/2024, 10:30 - Ana: Hola\nsegunda línea\n5/12/24, 9:10 PM - Tú: Reply"
        messages, is_group = WhatsAppParser("Ana").parse(text)
        self.assertFalse(is_group)
        self.assertEqual(messages[0].timestamp, datetime(2024, 5, 12, 10, 30))
        self.assertEqual(messages[0].text, "Hola\nsegunda línea")
        self.assertFalse(messages[0].is_outgoing)
        self.assertTrue(messages[1].is_outgoing)

    def test_detects_group_and_respects_my_name(self) -> None:
        text = "12/05/2024, 10:30 - Ana: Hola\n12/05/2024, 10:31 - Luis: Hola\n12/05/2024, 10:32 - María: Hola"
        messages, is_group = WhatsAppParser("Familia", my_name="María").parse(text)
        self.assertTrue(is_group)
        self.assertEqual([message.is_outgoing for message in messages], [False, False, True])

    def test_system_messages_are_marked(self) -> None:
        messages, _ = WhatsAppParser("Ana").parse("12/05/2024, 10:30 - Messages and calls are end-to-end encrypted.")
        self.assertTrue(messages[0].is_system)
        self.assertEqual(messages[0].sender, "Sistema")


class TestProcessingAndExports(unittest.TestCase):
    def test_zip_media_linking_filters_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "WhatsApp Chat with Ana.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("WhatsApp Chat with Ana.txt", "12/05/2024, 10:30 - Ana: canción IMG-0001.jpg (file attached)\n12/05/2024, 10:31 - Tú: <Media omitted>")
                output.writestr("IMG-0001.jpg", b"image")
                output.writestr("PTT-0001.opus", b"audio")
            conversation = process_chat_file(archive)
            self.assertEqual(len(conversation.media_files), 2)
            self.assertTrue(conversation.messages[0].attachment_path)
            self.assertTrue(conversation.messages[1].attachment_path)
            self.assertEqual(len(ChatFilter.apply(conversation.messages, text="CANCION")), 1)
            self.assertEqual(len(ChatFilter.apply(conversation.messages, has_media=True)), 2)
            output_dir = root / "output"
            exporter = ChatExporter(conversation)
            json_path = exporter.to_json(output_dir / "chat.json")
            csv_path = exporter.to_csv(output_dir / "chat.csv")
            xlsx_path = exporter.to_excel(output_dir / "chat.xlsx")
            report_path = exporter.to_summary_report(output_dir / "chat.md")
            self.assertTrue(xlsx_path.is_file())
            self.assertTrue(report_path.is_file())
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["messages"][0]["attachment_path"], conversation.messages[0].attachment_path)
            with csv_path.open(encoding="utf-8-sig", newline="") as input_file:
                self.assertIn("Ruta del archivo", next(csv.reader(input_file)))


if __name__ == "__main__":
    unittest.main()
