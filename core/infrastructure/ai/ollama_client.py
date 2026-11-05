"""Cliente HTTP local compatible con clasificación y servicios IA."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = os.environ.get(
    "UI_AUTOMATION_OLLAMA_URL",
    os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
)
DEFAULT_OLLAMA_MODEL = os.environ.get(
    "ANDROID_AI_MODEL",
    os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b"),
)


class OllamaError(RuntimeError):
    """Error controlado al comunicarse con Ollama."""


class OllamaClient:
    """Cliente pequeño para los endpoints ``/api/chat`` y ``/api/generate``."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 4.5,
        default_model: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.model = model or default_model or DEFAULT_OLLAMA_MODEL
        self.default_model = self.model
        self.timeout = max(0.5, float(timeout))
        # Usar el módulo por defecto mantiene compatibilidad con mocks existentes;
        # una sesión inyectada permite tests y consumidores con transporte propio.
        self._session = session or requests

    def is_available(self, timeout: float = 2.0) -> bool:
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=timeout)
            response.raise_for_status()
            return True
        except Exception:
            return False

    def healthcheck(self) -> bool:
        """Alias compatible con la API de clasificación."""
        return self.is_available(timeout=min(2.0, self.timeout))

    def list_models(self, timeout: float = 2.0) -> List[str]:
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            return [str(item.get("name")) for item in payload.get("models", []) if item.get("name")]
        except Exception:
            return []

    def chat(
        self,
        model: Optional[str],
        messages: List[Dict[str, str]],
        timeout: Optional[float] = None,
    ) -> str:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 180},
        }
        try:
            response = self._session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise OllamaError("Ollama devolvió una respuesta sin contenido")
            return content
        except OllamaError:
            raise
        except requests.Timeout as exc:
            raise OllamaError("Tiempo de espera agotado al consultar Ollama") from exc
        except requests.RequestException as exc:
            raise OllamaError(f"Error HTTP de Ollama: {exc}") from exc
        except Exception as exc:
            raise OllamaError(f"Ollama no disponible: {exc}") from exc

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system
        try:
            response = self._session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            content = response.json().get("response")
            if not isinstance(content, str):
                raise OllamaError("Ollama devolvió una respuesta inválida")
            return content
        except OllamaError:
            raise
        except requests.Timeout as exc:
            raise OllamaError("Tiempo de espera agotado al consultar Ollama") from exc
        except requests.RequestException as exc:
            raise OllamaError(f"Error HTTP de Ollama: {exc}") from exc
        except Exception as exc:
            raise OllamaError(f"Ollama no disponible: {exc}") from exc
