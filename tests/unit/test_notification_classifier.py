from unittest.mock import Mock

from ui.services.ai_models import NotificationLabel
from ui.services.notification_classifier import NotificationClassifier
from ui.services.notification_rules_fallback import classify_rules
from ui.services.ollama_client import OllamaError


def test_rules_extract_otp_and_amount():
    result = classify_rules("com.banco.demo", "Tu código es 123-456. Cargo $250 MXN")
    assert NotificationLabel.OTP in result.labels
    assert result.extract.otp == "123456"
    assert result.extract.amount == "$250 MXN"
    assert NotificationLabel.BANCO in result.labels


def test_classifier_uses_ollama_json():
    client = Mock(model="qwen2.5-coder:3b")
    client.chat.return_value = '{"labels":["ROMANTICO","AFECTO"],"primary":"ROMANTICO","confidence":0.91,"reason":"lenguaje afectivo","extract":{"otp":null,"amount":null}}'
    result = NotificationClassifier(client=client, enabled=True).classify("com.chat", "Te quiero mucho", key="k1")
    assert result.source == "ollama"
    assert result.primary == NotificationLabel.ROMANTICO
    assert NotificationLabel.AFECTO in result.labels


def test_invalid_or_timeout_ollama_falls_back_to_rules():
    client = Mock(model="test:3b")
    client.chat.side_effect = OllamaError("timeout")
    result = NotificationClassifier(client=client, enabled=True).classify("com.sms", "Código OTP 654321", key="k2")
    assert result.source == "rules"
    assert result.extract.otp == "654321"
    client.chat.assert_called_once()


def test_disabled_ai_does_not_call_ollama():
    client = Mock(model="test:3b")
    result = NotificationClassifier(client=client, enabled=False).classify("com.app", "Oferta gratis", key="k3")
    assert result.source == "rules"
    assert NotificationLabel.SPAM in result.labels
    client.chat.assert_not_called()
