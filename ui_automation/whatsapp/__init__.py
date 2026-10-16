"""Módulo de automatización y extracción de WhatsApp."""

from .client import WhatsAppClient
from .export import WhatsAppExporter, ChatExportError
from .chats import WhatsAppChatManager, ChatExportSummary
from .receiver import PACKAGE_NAME, ReceiverError, install_receiver, is_receiver_installed, uninstall_receiver

__all__ = [
    "WhatsAppClient",
    "WhatsAppExporter",
    "ChatExportError",
    "WhatsAppChatManager",
    "ChatExportSummary",
    "PACKAGE_NAME",
    "ReceiverError",
    "install_receiver",
    "is_receiver_installed",
    "uninstall_receiver",
]
