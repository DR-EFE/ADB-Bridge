"""Gestión del ciclo de vida del servicio local Ollama."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("ui_automation.ollama")

DEFAULT_OLLAMA_URL = os.environ.get("UI_AUTOMATION_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


class OllamaServiceManager:
    """Gestiona la detección, inicio y detención del servicio Ollama local."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL):
        self.base_url = base_url.rstrip("/")
        self._started_by_us = False
        self._process: Optional[subprocess.Popen] = None

    def is_running(self, timeout: float = 1.0) -> bool:
        """Verifica si Ollama está respondiendo en su puerto HTTP."""
        if requests is None:
            return False
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def _find_ollama_executable(self) -> Optional[str]:
        """Busca el ejecutable de ollama en PATH o rutas comunes de Windows."""
        exe = shutil.which("ollama")
        if exe:
            return exe

        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
            os.path.expandvars(r"%ProgramFiles%\Ollama\ollama.exe"),
            r"C:\Users\Desarrollo2\AppData\Local\Programs\Ollama\ollama.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    def start_if_needed(self, wait_seconds: float = 6.0) -> bool:
        """
        Verifica si Ollama está corriendo.
        Si NO está corriendo, intenta iniciarlo en segundo plano.
        Registra si fue iniciado por nosotros para cerrarlo luego.
        """
        if self.is_running(timeout=1.0):
            logger.info("Ollama ya se encuentra en ejecución en %s.", self.base_url)
            self._started_by_us = False
            return True

        exe = self._find_ollama_executable()
        if not exe:
            logger.info("Ollama no está en ejecución y no se encontró el ejecutable local. Se utilizará clasificación por reglas.")
            return False

        logger.info("Iniciando servicio local de Ollama en segundo plano (%s)...", exe)
        try:
            creationflags = 0
            if os.name == "nt":
                # DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP para no bloquear la consola
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

            self._process = subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                shell=False,
            )
            self._started_by_us = True

            # Esperar a que el servidor HTTP responda
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                if self.is_running(timeout=0.5):
                    logger.info("Ollama iniciado y listo para recibir peticiones.")
                    return True
                time.sleep(0.4)

            logger.warning("Ollama fue lanzado pero no respondió en %s s. Se continuará con fallback.", wait_seconds)
            return False
        except Exception as exc:
            logger.warning("No se pudo iniciar Ollama automáticamente: %s. Se usará fallback a reglas.", exc)
            self._started_by_us = False
            self._process = None
            return False

    def stop_if_started_by_us(self) -> None:
        """Detiene Ollama ÚNICAMENTE si nosotros lo arrancamos."""
        if not self._started_by_us:
            return

        logger.info("Deteniendo servicio local de Ollama (iniciado por esta sesión)...")
        try:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            elif os.name == "nt":
                # Si se lanzó como subproceso desvinculado, finalizarlo por taskkill
                subprocess.run(
                    ["taskkill", "/F", "/IM", "ollama.exe", "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            logger.info("Ollama detenido correctamente.")
        except Exception as exc:
            logger.debug("Error al detener Ollama: %s", exc)
        finally:
            self._started_by_us = False
            self._process = None
