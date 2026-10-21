"""Unit tests for AutomationSuggestionService."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ui.services.ai_report_models import AutomationSuggestion, AutomationSuggestionStatus
from ui.services.automation_rules import AutomationRule, TriggerType
from ui.services.automation_service import AutomationService
from ui.services.automation_suggestion_service import AutomationSuggestionService
from ui.services.database import SQLiteStore
from ui.services.ollama_client import OllamaClient


@pytest.fixture
def temp_db(tmp_path: Path) -> SQLiteStore:
    db_path = tmp_path / "test_suggestions.db"
    return SQLiteStore(str(db_path))


@pytest.fixture
def temp_notifications_dir(tmp_path: Path) -> Path:
    notif_dir = tmp_path / "notifications"
    notif_dir.mkdir(parents=True, exist_ok=True)
    return notif_dir


def test_suggest_financial_alert_rule(temp_db, temp_notifications_dir):
    # Add an important bank notification
    important_data = [
        {
            "detected_at": "2026-10-21 14:00:00",
            "tipo": "BANCO_ALERTA",
            "appName": "Banco Azteca",
            "title": "Retiro sin tarjeta",
            "text": "Se ha generado un retiro de $500.",
        }
    ]
    imp_file = temp_notifications_dir / "notificaciones_importantes.json"
    imp_file.write_text(json.dumps(important_data), encoding="utf-8")

    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.generate.return_value = "Rationale: Proteger la auditoria de movimientos bancarios notificando al usuario."

    service = AutomationSuggestionService(
        db_store=temp_db,
        notifications_dir=temp_notifications_dir,
        ollama_client=mock_ollama,
    )

    suggestions = service.generate_suggestions()
    assert len(suggestions) >= 1

    bank_sugg = next((s for s in suggestions if "Financiera" in s.title or "Bancaria" in s.title), None)
    assert bank_sugg is not None
    assert bank_sugg.confirm_required is True
    assert bank_sugg.status == AutomationSuggestionStatus.PENDING
    assert bank_sugg.proposed_rule["trigger_type"] == TriggerType.NOTIFICATION_RECEIVED.value
    assert "Retiro" in bank_sugg.rationale or "Proteger" in bank_sugg.rationale


def test_suggest_device_connected_and_accept_rule(temp_db, temp_notifications_dir):
    mock_automation_service = MagicMock(spec=AutomationService)

    service = AutomationSuggestionService(
        db_store=temp_db,
        automation_service=mock_automation_service,
        notifications_dir=temp_notifications_dir,
        ollama_client=None,
    )

    # Force generate device connected suggestion via context
    context = {"device_connected_count": 8}
    suggestions = service.generate_suggestions(context=context)

    conn_sugg = next((s for s in suggestions if "Conexión de Dispositivo" in s.title), None)
    assert conn_sugg is not None
    assert conn_sugg.confirm_required is True
    assert conn_sugg.status == AutomationSuggestionStatus.PENDING

    # Test accept_suggestion
    accepted = service.accept_suggestion(conn_sugg.id)
    assert accepted.status == AutomationSuggestionStatus.ACCEPTED

    # Verify automation_service.save_rule was called with an AutomationRule
    mock_automation_service.save_rule.assert_called_once()
    saved_rule = mock_automation_service.save_rule.call_args[0][0]
    assert isinstance(saved_rule, AutomationRule)
    assert saved_rule.trigger_type == TriggerType.DEVICE_CONNECTED


def test_dismiss_suggestion_and_cooldown(temp_db, temp_notifications_dir):
    service = AutomationSuggestionService(
        db_store=temp_db,
        notifications_dir=temp_notifications_dir,
        ollama_client=None,
    )

    context = {"device_connected_count": 5}
    suggestions = service.generate_suggestions(context=context)
    assert len(suggestions) > 0

    target = suggestions[0]
    dismissed = service.dismiss_suggestion(target.id, cooldown_days=7)
    assert dismissed.status == AutomationSuggestionStatus.DISMISSED

    # Next generation with same context should NOT regenerate the dismissed suggestion
    new_suggestions = service.generate_suggestions(context=context)
    matching = [s for s in new_suggestions if s.signature == target.signature]
    assert len(matching) == 0


def test_accept_suggestion_without_automation_service(temp_db, temp_notifications_dir):
    service = AutomationSuggestionService(
        db_store=temp_db,
        automation_service=None,
        notifications_dir=temp_notifications_dir,
    )

    context = {"device_connected_count": 5}
    suggestions = service.generate_suggestions(context=context)
    assert len(suggestions) > 0

    sugg = suggestions[0]
    accepted = service.accept_suggestion(sugg.id)
    assert accepted.status == AutomationSuggestionStatus.ACCEPTED
    assert accepted.proposed_rule is not None
