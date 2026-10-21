"""Clasificación determinista sin depender de Ollama."""
from __future__ import annotations

import re
from typing import Optional

from .ai_models import NotificationClassification, NotificationExtract, NotificationLabel

OTP_RE = re.compile(r"(?<!\d)(\d{3}[\s-]\d{3}|\d{4,8})(?!\d)")
AMOUNT_RE = re.compile(r"(?:\$\s?[\d,.]+\s?(?:MXN|USD|EUR)?|[\d,.]+\s?(?:MXN|USD|EUR))", re.I)

KEYWORDS = {
    NotificationLabel.BANCO: ("banco", "transferencia", "cargo", "compra", "tarjeta", "mercadopago", "paypal"),
    NotificationLabel.URGENTE: ("urgente", "inmediato", "seguridad", "fraude", "bloqueado", "alerta"),
    NotificationLabel.SPAM: ("oferta", "promo", "descuento", "cupón", "gratis", "% off", "gana"),
    NotificationLabel.SISTEMA: ("batería", "depuración inalámbrica", "actualización", "android system"),
    NotificationLabel.SEXUAL: ("sexo", "desnudo", "nudes", "desnuda", "erótico", "erótica"),
    NotificationLabel.OFENSIVO: ("idiota", "imbécil", "estúpido", "pendejo", "puto", "zorra"),
    NotificationLabel.ROMANTICO: ("te amo", "te quiero", "mi amor", "amor de mi vida", "enamorad"),
    NotificationLabel.COQUETEO: ("guapo", "guapa", "precioso", "preciosa", "beso", "cita", "ligar"),
    NotificationLabel.AFECTO: ("te extraño", "te echo de menos", "cariño", "corazón", "abrazo"),
}


def classify_rules(package: str, text: str, ts: Optional[str] = None) -> NotificationClassification:
    normalized = f"{package} {text}".casefold()
    labels = []
    otp_match = OTP_RE.search(text or "")
    amount_match = AMOUNT_RE.search(text or "")
    if otp_match:
        labels.append(NotificationLabel.OTP)
    for label, words in KEYWORDS.items():
        if any(word.casefold() in normalized for word in words):
            labels.append(label)
    if not labels:
        labels = [NotificationLabel.NORMAL]
    priority = [NotificationLabel.OTP, NotificationLabel.BANCO, NotificationLabel.URGENTE,
                NotificationLabel.SEXUAL, NotificationLabel.OFENSIVO, NotificationLabel.SPAM,
                NotificationLabel.ROMANTICO, NotificationLabel.COQUETEO, NotificationLabel.AFECTO,
                NotificationLabel.SISTEMA, NotificationLabel.NORMAL]
    primary = next(label for label in priority if label in labels)
    confidence = 0.98 if otp_match else (0.84 if primary != NotificationLabel.NORMAL else 0.55)
    return NotificationClassification(
        labels=labels, primary=primary, confidence=confidence,
        reason="Clasificación determinista por reglas y expresiones configuradas.",
        extract=NotificationExtract(otp=otp_match.group(1).replace(" ", "").replace("-", "") if otp_match else None,
                                     amount=amount_match.group(0) if amount_match else None),
        package=package, text=text, ts=ts or NotificationClassification.model_fields["ts"].default_factory(), source="rules",
    )
