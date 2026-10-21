"""Clasificador multietiqueta Ollama con fallback determinista."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from .ai_models import NotificationClassification, NotificationExtract, NotificationLabel
from .notification_rules_fallback import classify_rules
from .ollama_client import OllamaClient, OllamaError


PROMPT = """Clasifica esta notificación de Android. Responde SOLO JSON válido, sin markdown:
{"labels":["NORMAL"],"primary":"NORMAL","confidence":0.0,"reason":"breve","extract":{"otp":null,"amount":null}}
Labels permitidas: NORMAL, AFECTO, ROMANTICO, COQUETEO, SEXUAL, OFENSIVO, URGENTE, OTP, BANCO, SPAM, SISTEMA.
Puedes usar varias labels. No inventes OTP ni importes; usa null si no aparecen literalmente.
App: {package}
Texto: {text}"""


class NotificationClassifier:
    def __init__(self, client: Optional[OllamaClient] = None, enabled: Optional[bool] = None,
                 review_threshold: Optional[float] = None, strong_threshold: Optional[float] = None) -> None:
        self.client = client or OllamaClient()
        self.enabled = (os.getenv("ANDROID_AI_ENABLED", "true").lower() in ("1", "true", "yes", "si")) if enabled is None else enabled
        self.review_threshold = float(os.getenv("ANDROID_AI_REVIEW_THRESHOLD", "0.5")) if review_threshold is None else review_threshold
        self.strong_threshold = float(os.getenv("ANDROID_AI_STRONG_THRESHOLD", "0.75")) if strong_threshold is None else strong_threshold
        self._classified_cache: dict[str, NotificationClassification] = {}

    def classify(self, package: str, text: str, *, key: Optional[str] = None, ts: Optional[str] = None) -> NotificationClassification:
        cache_key = key or f"{package}|{text}"
        # La clasificación es pura para el mismo evento durante esta sesión.
        if cache_key in self._classified_cache:
            return self._classified_cache[cache_key]
        fallback = classify_rules(package, text, ts)
        if not self.enabled:
            self._classified_cache[cache_key] = fallback
            return fallback
        try:
            prompt = PROMPT.replace("{package}", package).replace("{text}", text[:1200])
            raw = self.client.chat(None, [{"role": "user", "content": prompt}])
            parsed = self._parse_json(raw)
            result = NotificationClassification(
                labels=parsed.get("labels", ["NORMAL"]), primary=parsed.get("primary", "NORMAL"),
                confidence=parsed.get("confidence", 0), reason=str(parsed.get("reason", ""))[:300],
                extract=parsed.get("extract") or {}, model=self.client.model, package=package, text=text,
                ts=ts or fallback.ts, source="ollama",
            )
            if result.confidence < self.review_threshold:
                result = fallback.model_copy(update={"reason": "Confianza IA baja; se usa clasificación por reglas."})
            self._classified_cache[cache_key] = result
            return result
        except (OllamaError, ValueError, TypeError, json.JSONDecodeError):
            self._classified_cache[cache_key] = fallback
            return fallback
        except Exception:
            self._classified_cache[cache_key] = fallback
            return fallback

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Respuesta de Ollama no contiene JSON.")
        value = json.loads(cleaned[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("La clasificación no es un objeto JSON.")
        return value
