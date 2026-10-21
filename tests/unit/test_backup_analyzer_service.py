"""Unit tests for BackupAnalyzerService."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ui.services.ai_report_models import BackupAnalysisReport
from ui.services.backup_analyzer_service import BackupAnalyzerService
from ui.services.ollama_client import OllamaClient, OllamaError


@pytest.fixture
def sample_backup_dirs(tmp_path: Path):
    # Backup 1
    b1 = tmp_path / "backup_20261020_120000"
    b1.mkdir()
    manifest_1 = {
        "BackupName": "Backup_Full_1",
        "Timestamp": "2026-10-20T12:00:00",
        "Categories": [
            {"Name": "Contacts", "Status": "OK", "TotalFiles": 150},
            {"Name": "SMS", "Status": "DENIED", "TotalFiles": 0},
            {"Name": "WhatsApp", "Status": "PARTIAL", "TotalFiles": 40},
        ],
    }
    errors_1 = [
        {
            "category": "SMS",
            "path": "/data/data/com.android.providers.telephony/databases/mmssms.db",
            "code": "EACCES",
            "message": "Permission denied: Root or system app privileges required",
        },
        {
            "category": "WhatsApp",
            "path": "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
            "code": "EPERM",
            "message": "Scoped storage restrictions",
        },
    ]
    (b1 / "MANIFEST.json").write_text(json.dumps(manifest_1), encoding="utf-8")
    (b1 / "errors.json").write_text(json.dumps(errors_1), encoding="utf-8")

    # Backup 2
    b2 = tmp_path / "backup_20261021_120000"
    b2.mkdir()
    manifest_2 = {
        "BackupName": "Backup_Full_2",
        "Timestamp": "2026-10-21T12:00:00",
        "Categories": [
            {"Name": "Contacts", "Status": "OK", "TotalFiles": 155},
            {"Name": "SMS", "Status": "DENIED", "TotalFiles": 0},
            {"Name": "WhatsApp", "Status": "PARTIAL", "TotalFiles": 42},
        ],
    }
    errors_2 = [
        {
            "category": "SMS",
            "path": "/data/data/com.android.providers.telephony/databases/mmssms.db",
            "code": "EACCES",
            "message": "Permission denied: Root or system app privileges required",
        },
        {
            "category": "WhatsApp",
            "path": "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
            "code": "EPERM",
            "message": "Scoped storage restrictions",
        },
    ]
    (b2 / "MANIFEST.json").write_text(json.dumps(manifest_2), encoding="utf-8")
    (b2 / "errors.json").write_text(json.dumps(errors_2), encoding="utf-8")

    return [b1, b2]


def test_backup_analyzer_repeated_failures(sample_backup_dirs):
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.generate.return_value = "Resumen LLM: Se detectan bloqueos persistentes en SMS y WhatsApp Scoped Storage."

    service = BackupAnalyzerService(ollama_client=mock_ollama, threshold_repeated=2)
    report = service.analyze_backups(sample_backup_dirs)

    assert isinstance(report, BackupAnalysisReport)
    assert report.total_backups_analyzed == 2
    assert report.total_errors >= 4

    # Check repeated failure detection
    assert any("mmssms.db" in p for p in report.repeated_denied_paths)

    # Check findings
    assert len(report.findings) >= 2
    severities = [f.severity for f in report.findings]
    assert "ERROR" in severities or "WARNING" in severities

    # Check recommendations (template rules applied)
    assert len(report.recommendations) >= 1
    recs_text = " ".join(r.recommendation.lower() for r in report.recommendations)
    assert "sms" in recs_text or "storage" in recs_text or "whatsapp" in recs_text

    # Check LLM narrative
    assert "Resumen LLM" in report.summary_text


def test_backup_analyzer_ollama_fallback(sample_backup_dirs):
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.generate.side_effect = OllamaError("Model timeout")

    service = BackupAnalyzerService(ollama_client=mock_ollama, threshold_repeated=2)
    report = service.analyze_backups(sample_backup_dirs)

    assert isinstance(report, BackupAnalysisReport)
    assert report.total_backups_analyzed == 2
    assert report.summary_text is not None
    assert "Se analizaron 2 respaldos" in report.summary_text


def test_backup_analyzer_clean_backup(tmp_path: Path):
    clean_dir = tmp_path / "backup_clean"
    clean_dir.mkdir()
    manifest = {
        "BackupName": "Backup_Clean",
        "Timestamp": "2026-10-21T10:00:00",
        "Categories": [
            {"Name": "Contacts", "Status": "OK", "TotalFiles": 50},
            {"Name": "Photos", "Status": "OK", "TotalFiles": 200},
        ],
    }
    (clean_dir / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (clean_dir / "errors.json").write_text("[]", encoding="utf-8")

    service = BackupAnalyzerService(ollama_client=None)
    report = service.analyze_backups([clean_dir])

    assert report.total_backups_analyzed == 1
    assert report.total_errors == 0
    assert len(report.findings) == 1
    assert "saludables" in report.findings[0].description
