"""Módulo de automatización y extracción de WhatsApp."""

from .chats import ChatExportSummary, WhatsAppChatManager
from .client import WhatsAppClient
from .export import ChatExportError, ShareSheetResult, WhatsAppExporter
from .receiver import (
    PACKAGE_NAME,
    ReceiverError,
    ensure_receiver_ready,
    install_receiver,
    is_receiver_installed,
    uninstall_receiver,
)

__all__ = [
    "WhatsAppClient",
    "WhatsAppExporter",
    "ChatExportError",
    "ShareSheetResult",
    "WhatsAppChatManager",
    "ChatExportSummary",
    "PACKAGE_NAME",
    "ReceiverError",
    "ensure_receiver_ready",
    "install_receiver",
    "is_receiver_installed",
    "uninstall_receiver",
]
