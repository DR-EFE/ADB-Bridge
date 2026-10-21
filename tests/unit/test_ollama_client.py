from unittest.mock import Mock, patch

from ui.services.ollama_client import OllamaClient, OllamaError


def test_chat_returns_message_content():
    response = Mock()
    response.json.return_value = {"message": {"content": "{\"labels\": [\"NORMAL\"]}"}}
    response.raise_for_status.return_value = None
    with patch("ui.services.ollama_client.requests.post", return_value=response) as post:
        result = OllamaClient(model="test:3b").chat(None, [{"role": "user", "content": "hola"}])
    assert result.startswith("{")
    assert post.call_args.kwargs["json"]["model"] == "test:3b"


def test_chat_converts_transport_failure_to_controlled_error():
    with patch("ui.services.ollama_client.requests.post", side_effect=TimeoutError("timeout")):
        try:
            OllamaClient().chat(None, [])
        except OllamaError as exc:
            assert "Ollama no disponible" in str(exc)
        else:
            raise AssertionError("Se esperaba OllamaError")


def test_healthcheck_returns_false_when_ollama_is_down():
    with patch("ui.services.ollama_client.requests.get", side_effect=OSError("offline")):
        assert OllamaClient().healthcheck() is False
