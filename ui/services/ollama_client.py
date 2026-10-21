"""Cliente HTTP local para interactuar con Ollama (Fase B - IA).

Provee métodos para:
- Comprobación de disponibilidad (is_available)
- Listado de modelos instalados (list_models)
- Generación de texto (generate) con soporte de modelo, prompt de sistema y temperatura
- Manejo limpio de timeouts y excepciones de conexión para permitir degradación elegante
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("ui.services.ollama_client")

# URL por defecto de Ollama en local (127.0.0.1 evita demoras de resolución IPv6 en Windows)
DEFAULT_OLLAMA_URL = os.environ.get(
    "UI_AUTOMATION_OLLAMA_URL",
    os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
).rstrip("/")

DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")


class OllamaError(RuntimeError):
    """Excepción específica para fallos de comunicación con el servicio Ollama."""
    pass


class OllamaClient:
    """Cliente para la API REST de Ollama local."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        default_model: str = DEFAULT_OLLAMA_MODEL,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._session = session or requests.Session()

    def is_available(self, timeout: float = 2.0) -> bool:
        """Verifica si el servidor Ollama está activo y respondiendo."""
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=timeout)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("Ollama no disponible en %s: %s", self.base_url, exc)
            return False

    def list_models(self, timeout: float = 2.0) -> List[str]:
        """Obtiene la lista de modelos descargados localmente en Ollama."""
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=timeout)
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = data.get("models", [])
            return [m.get("name") for m in models if isinstance(m, dict) and "name" in m]
        except Exception as exc:
            logger.debug("Error listando modelos de Ollama: %s", exc)
            return []

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> str:
        """
        Envía un prompt a Ollama y retorna la respuesta generada en texto.
        Lanza OllamaError si falla la conexión, se excede el timeout o devuelve error HTTP.
        """
        chosen_model = model or self.default_model
        payload: Dict[str, Any] = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if system:
            payload["system"] = system

        url = f"{self.base_url}/api/generate"
        try:
            resp = self._session.post(url, json=payload, timeout=timeout)
            if resp.status_code != 200:
                raise OllamaError(f"Ollama devolvió código HTTP {resp.status_code}: {resp.text}")
            data = resp.json()
            return data.get("response", "").strip()
        except requests.Timeout as exc:
            raise OllamaError(f"Timeout ({timeout}s) agotado esperando respuesta de Ollama: {exc}") from exc
        except requests.RequestException as exc:
            raise OllamaError(f"Error de conexión con Ollama en {url}: {exc}") from exc
        except Exception as exc:
            raise OllamaError(f"Fallo inesperado al generar con Ollama: {exc}") from exc
