"""Cliente pequeño y controlado para Ollama local."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - el entorno de producción incluye requests
    requests = None


class OllamaError(RuntimeError):
    """Error recuperable de disponibilidad, timeout o respuesta de Ollama."""


class OllamaClient:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None, timeout: float = 4.5) -> None:
        self.base_url = (base_url or os.getenv("UI_AUTOMATION_OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.getenv("ANDROID_AI_MODEL", "qwen2.5-coder:3b")
        self.timeout = max(0.5, float(timeout))

    def healthcheck(self) -> bool:
        if requests is None:
            return False
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=min(1.5, self.timeout))
            return response.status_code == 200
        except Exception:
            return False

    def chat(self, model: Optional[str], messages: List[Dict[str, str]], timeout: Optional[float] = None) -> str:
        if requests is None:
            raise OllamaError("requests no está instalado; Ollama no está disponible.")
        selected_model = model or self.model
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={"model": selected_model, "messages": messages, "stream": False,
                      "options": {"temperature": 0, "num_predict": 180}},
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            payload: Any = response.json()
            content = ((payload.get("message") or {}).get("content") if isinstance(payload, dict) else None)
            if not isinstance(content, str) or not content.strip():
                raise OllamaError("Ollama devolvió una respuesta sin contenido.")
            return content.strip()
        except OllamaError:
            raise
        except Exception as exc:
            raise OllamaError(f"Ollama no disponible: {exc}") from exc
