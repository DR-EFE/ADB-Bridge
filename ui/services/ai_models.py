"""Contratos tipados para clasificación local de notificaciones."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class NotificationLabel(str, Enum):
    NORMAL = "NORMAL"
    AFECTO = "AFECTO"
    ROMANTICO = "ROMANTICO"
    COQUETEO = "COQUETEO"
    SEXUAL = "SEXUAL"
    OFENSIVO = "OFENSIVO"
    URGENTE = "URGENTE"
    OTP = "OTP"
    BANCO = "BANCO"
    SPAM = "SPAM"
    SISTEMA = "SISTEMA"


class NotificationExtract(BaseModel):
    otp: Optional[str] = None
    amount: Optional[str] = None


class NotificationClassification(BaseModel):
    labels: List[NotificationLabel] = Field(default_factory=lambda: [NotificationLabel.NORMAL])
    primary: NotificationLabel = NotificationLabel.NORMAL
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    extract: NotificationExtract = Field(default_factory=NotificationExtract)
    model: Optional[str] = None
    package: str = ""
    text: str = ""
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = Field(default="rules", pattern=r"^(ollama|rules)$")

    @field_validator("labels")
    @classmethod
    def unique_labels(cls, value: List[NotificationLabel]) -> List[NotificationLabel]:
        result: List[NotificationLabel] = []
        for label in value:
            if label not in result:
                result.append(label)
        return result or [NotificationLabel.NORMAL]

    def to_record(self) -> dict:
        return self.model_dump(mode="json")
