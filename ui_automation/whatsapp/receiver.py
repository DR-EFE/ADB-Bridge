"""Instalación y limpieza del receptor local de exportaciones de WhatsApp."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ui_automation.whatsapp.receiver")

PACKAGE_NAME = "com.androidtools.receiver"
APK_PATH = Path(__file__).resolve().parents[2] / "androidtools_receiver" / "build" / "AndroidToolsReceiver.apk"


class ReceiverError(RuntimeError):
    """Error al instalar o retirar el receptor local."""


def _adb_command(serial: Optional[str], *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    adb = shutil.which("adb")
    if not adb:
        raise ReceiverError("No se encontró adb en PATH.")
    command = [adb]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ReceiverError(f"ADB no respondió a tiempo: {' '.join(args)}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReceiverError(detail or f"ADB falló: {' '.join(args)}")
    return result


def is_receiver_installed(serial: Optional[str] = None) -> bool:
    try:
        result = _adb_command(serial, "shell", "pm", "path", PACKAGE_NAME, timeout=15)
        return bool(result.stdout.strip())
    except ReceiverError:
        return False


def install_receiver(serial: Optional[str] = None) -> None:
    """Instala el APK sin crear un icono de lanzador."""
    if not APK_PATH.is_file():
        raise ReceiverError(
            f"No se encontró la APK auxiliar en {APK_PATH}. "
            "Ejecuta androidtools_receiver\\build_receiver.ps1 primero."
        )
    remote = "/data/local/tmp/AndroidToolsReceiver.apk"
    _adb_command(serial, "push", str(APK_PATH), remote, timeout=90)
    try:
        _adb_command(serial, "shell", "pm", "install", "-r", "-g", remote, timeout=90)
    finally:
        try:
            _adb_command(serial, "shell", "rm", "-f", remote, timeout=15)
        except ReceiverError:
            logger.debug("No se pudo limpiar el APK temporal remoto.", exc_info=True)


def uninstall_receiver(serial: Optional[str] = None) -> None:
    """Retira el receptor cuando terminan todas las exportaciones."""
    _adb_command(serial, "uninstall", PACKAGE_NAME, timeout=60)
