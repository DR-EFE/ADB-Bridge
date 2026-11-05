"""Perfiles declarativos del Centro de respaldos.

No contienen lógica ADB; BackupService ejecuta estas definiciones mediante el
contrato inyectado de AdbService.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class BackupProfileItem:
    id: str
    label: str
    kind: str  # pull | shell
    remote_path: Optional[str] = None
    command: Optional[str] = None
    output_name: Optional[str] = None


@dataclass(frozen=True)
class BackupProfile:
    id: str
    label: str
    description: str
    items: List[BackupProfileItem] = field(default_factory=list)


def get_backup_profiles(custom_folders: Optional[Iterable[str]] = None) -> Dict[str, BackupProfile]:
    """Devuelve perfiles seguros; datos privados pueden resultar DENIED según Android/OEM."""
    profiles = {
        "photos_videos": BackupProfile(
            "photos_videos", "Fotos y videos", "Medios compartidos del dispositivo.", [
                BackupProfileItem("dcim", "DCIM", "pull", "/sdcard/DCIM"),
                BackupProfileItem("pictures", "Pictures", "pull", "/sdcard/Pictures"),
                BackupProfileItem("movies", "Movies", "pull", "/sdcard/Movies"),
            ]),
        "whatsapp": BackupProfile(
            "whatsapp", "WhatsApp", "Archivos compartidos/exportados disponibles sin root.", [
                BackupProfileItem("whatsapp_media", "WhatsApp Media", "pull", "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"),
                BackupProfileItem("whatsapp_legacy", "WhatsApp legacy", "pull", "/sdcard/WhatsApp/Media"),
            ]),
        "documents": BackupProfile(
            "documents", "Documentos", "Descargas y documentos compartidos.", [
                BackupProfileItem("download", "Download", "pull", "/sdcard/Download"),
                BackupProfileItem("documents", "Documents", "pull", "/sdcard/Documents"),
            ]),
        "contacts_sms_calls": BackupProfile(
            "contacts_sms_calls", "Contactos, SMS y llamadas", "Best effort; Android puede negar estas consultas.", [
                BackupProfileItem("contacts", "Contactos", "shell", command="content query --uri content://com.android.contacts/contacts/", output_name="contacts.txt"),
                BackupProfileItem("sms", "SMS", "shell", command="content query --uri content://sms/", output_name="sms.txt"),
                BackupProfileItem("calls", "Llamadas", "shell", command="content query --uri content://call_log/calls", output_name="calls.txt"),
            ]),
        "installed_apps": BackupProfile(
            "installed_apps", "Aplicaciones instaladas", "Lista y metadatos; no extrae APKs protegidos.", [
                BackupProfileItem("packages", "Paquetes", "shell", command="pm list packages -f", output_name="packages.txt"),
                BackupProfileItem("packages_meta", "Metadatos", "shell", command="dumpsys package", output_name="packages_metadata.txt"),
            ]),
    }
    custom_items = [
        BackupProfileItem(f"custom_{index}", path, "pull", "/sdcard/" + path.strip("/"))
        for index, path in enumerate(custom_folders or []) if path and path.strip()
    ]
    if custom_items:
        profiles["custom"] = BackupProfile("custom", "Carpetas personalizadas", "Rutas compartidas indicadas por el usuario.", custom_items)
    return profiles


def resolve_profile(profile: str | BackupProfile, custom_folders: Optional[Iterable[str]] = None) -> BackupProfile:
    if isinstance(profile, BackupProfile):
        return profile
    profiles = get_backup_profiles(custom_folders)
    if profile not in profiles:
        raise KeyError(f"Perfil de backup desconocido: {profile}")
    return profiles[profile]
