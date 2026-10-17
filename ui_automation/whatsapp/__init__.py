"""Módulo de automatización y extracción de WhatsApp."""

from .chats import ChatExportSummary, WhatsAppChatManager
from .client import WhatsAppClient
from .export import ChatExportError, ShareSheetResult, WhatsAppExporter
from .receiver import (
    PACKAGE_NAME,
    ReceiverError,
    clear_failed_install_cache,
    ensure_receiver_ready,
    install_receiver,
    is_receiver_installed,
    uninstall_receiver,
)
from .processor import (
    ChatAnalytics,
    ChatConversation,
    ChatExporter,
    ChatFilter,
    ChatMessage,
    MediaAttachment,
    WhatsAppParser,
    WhatsAppUnpacker,
    export_processed_chat,
    process_chat_file,
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
    "ChatMessage",
    "MediaAttachment",
    "ChatAnalytics",
    "ChatConversation",
    "WhatsAppParser",
    "WhatsAppUnpacker",
    "ChatFilter",
    "ChatExporter",
    "process_chat_file",
    "export_processed_chat",
]
