"""Instalación, verificación y limpieza del receptor local de exportaciones de WhatsApp."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

from ..adb_manager import get_adb_executable

logger = logging.getLogger("ui_automation.whatsapp.receiver")

PACKAGE_NAME = "com.androidtools.receiver"
APK_PATH = Path(__file__).resolve().parents[2] / "androidtools_receiver" / "build" / "AndroidToolsReceiver.apk"

# Registro de intentos fallidos por serial para no reintentar en bucle
_FAILED_INSTALL_ATTEMPTS: Dict[str, str] = {}


class ReceiverError(RuntimeError):
    """Error al instalar o retirar el receptor local."""


def _safe_subprocess_flags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _adb_command(serial: Optional[str], *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    adb = get_adb_executable()
    command = [adb]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_safe_subprocess_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ReceiverError(f"ADB no respondió a tiempo: {' '.join(args)}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReceiverError(detail or f"ADB falló: {' '.join(args)}")
    return result


def is_receiver_installed(serial: Optional[str] = None) -> bool:
    """Verifica si el receptor AndroidTools Receiver está instalado."""
    try:
        result = _adb_command(serial, "shell", "pm", "path", PACKAGE_NAME, timeout=10.0)
        return "package:" in (result.stdout or "")
    except Exception as exc:
        logger.debug("Error comprobando paquete %s: %s", PACKAGE_NAME, exc)
        return False


def install_receiver(serial: Optional[str] = None, timeout: float = 30.0) -> bool:
    """
    Instala el APK auxiliar en el dispositivo y valida con pm path.
    Lanza ReceiverError con el motivo en caso de falla.
    """
    if not APK_PATH.is_file():
        raise ReceiverError(
            f"No se encontró el APK auxiliar en {APK_PATH}. "
            "Ejecuta androidtools_receiver\\build_receiver.ps1 para compilarlo."
        )

    remote = "/data/local/tmp/AndroidToolsReceiver.apk"
    logger.info("Subiendo APK auxiliar a %s...", remote)
    _adb_command(serial, "push", str(APK_PATH), remote, timeout=min(timeout, 20.0))

    try:
        logger.info("Instalando APK %s en el dispositivo...", PACKAGE_NAME)
        _adb_command(serial, "shell", "pm", "install", "-r", "-g", remote, timeout=timeout)
    finally:
        try:
            _adb_command(serial, "shell", "rm", "-f", remote, timeout=5.0)
        except Exception:
            pass

    # Validar que efectivamente quedó instalado
    if not is_receiver_installed(serial):
        raise ReceiverError("pm install terminó pero el paquete no aparece listado en pm path.")

    logger.info("AndroidTools Receiver instalado y validado con éxito.")
    return True


def uninstall_receiver(serial: Optional[str] = None) -> None:
    """Retira el receptor cuando terminan las exportaciones."""
    try:
        _adb_command(serial, "uninstall", PACKAGE_NAME, timeout=20.0)
        logger.info("AndroidTools Receiver desinstalado de %s.", serial or "dispositivo")
    except Exception as exc:
        logger.debug("Error desinstalando %s: %s", PACKAGE_NAME, exc)


def ensure_receiver_ready(serial: Optional[str] = None, timeout: float = 25.0) -> Tuple[bool, str]:
    """
    Comprueba si el receptor está instalado. Si falta, intenta instalarlo una sola vez.
    Si falla (bloqueo por contraseña, huella o error), no repite en bucle y devuelve el motivo
    para continuar con el fallback de administradores de archivos.

    :return: (is_ready, status_description)
    """
    key = serial or "default"

    # 1. Ya instalado
    if is_receiver_installed(serial):
        return True, "instalado"

    # 2. Si ya falló previamente en este dispositivo durante la sesión, no reintentar en bucle
    if key in _FAILED_INSTALL_ATTEMPTS:
        return False, f"no disponible ({_FAILED_INSTALL_ATTEMPTS[key]})"

    # 3. Comprobar si existe el APK local
    if not APK_PATH.is_file():
        reason = "APK no compilado en build/AndroidToolsReceiver.apk"
        _FAILED_INSTALL_ATTEMPTS[key] = reason
        logger.info("Auxiliar no disponible: %s. Se usará fallback de administradores de archivos.", reason)
        return False, f"no disponible ({reason})"

    # 4. Intentar instalación automática
    try:
        logger.info("Intentando instalación automática de AndroidTools Receiver en %s...", key)
        install_receiver(serial=serial, timeout=timeout)
        if key in _FAILED_INSTALL_ATTEMPTS:
            del _FAILED_INSTALL_ATTEMPTS[key]
        return True, "instalado automáticamente"
    except Exception as exc:
        reason = str(exc)
        _FAILED_INSTALL_ATTEMPTS[key] = reason
        logger.warning(
            "Fallo en la instalación automática del auxiliar (%s): %s. "
            "Continuando con fallback de administradores de archivos.",
            key,
            exc,
        )
        return False, f"instalación fallida ({reason})"


def clear_failed_install_cache(serial: Optional[str] = None) -> None:
    """Limpia la caché de intentos fallidos si el usuario fuerza un reintento manual."""
    if serial:
        _FAILED_INSTALL_ATTEMPTS.pop(serial, None)
    else:
        _FAILED_INSTALL_ATTEMPTS.clear()
