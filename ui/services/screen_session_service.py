"""Manage external scrcpy sessions for connected Android devices."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Sequence

from .adb_service import AdbService

logger = logging.getLogger("ui.services.screen_session_service")

STARTUP_CONFIRMATION_SECONDS = 2.0
OUTPUT_JOIN_SECONDS = 2.0
PROCESS_STOP_TIMEOUT_SECONDS = 5.0
MAX_LOG_LINES = 200


class ScreenSessionError(RuntimeError):
    """An actionable error while starting or managing a scrcpy session."""


class ScreenSessionState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class ScreenPreset(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    NATIVE = "native"


@dataclass(frozen=True)
class ScreenPresetConfig:
    label: str
    max_size: int
    max_fps: int
    video_bit_rate: str


SCREEN_PRESETS: Dict[ScreenPreset, ScreenPresetConfig] = {
    ScreenPreset.LOW: ScreenPresetConfig("Baja", 800, 30, "4M"),
    ScreenPreset.MEDIUM: ScreenPresetConfig("Media", 1280, 60, "8M"),
    ScreenPreset.HIGH: ScreenPresetConfig("Alta", 1920, 60, "12M"),
    # Reserved for a future UI option. max-size 0 asks scrcpy to use native size.
    ScreenPreset.NATIVE: ScreenPresetConfig("Nativa", 0, 60, "20M"),
}


@dataclass
class ScreenSession:
    serial: str
    preset: ScreenPreset
    state: ScreenSessionState = ScreenSessionState.STARTING
    process: Optional[subprocess.Popen] = None
    started_at: float = field(default_factory=time.monotonic)
    error: Optional[str] = None
    command: Sequence[str] = field(default_factory=tuple)
    logs: Deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    reader_thread: Optional[threading.Thread] = None
    monitor_thread: Optional[threading.Thread] = None
    stop_requested: threading.Event = field(default_factory=threading.Event)


class ScreenSessionService:
    """Start one scrcpy process per explicitly selected, ready ADB serial."""

    def __init__(
        self,
        adb_service: Optional[AdbService] = None,
        app_root: Optional[Path] = None,
        on_change: Optional[Callable[[ScreenSession], None]] = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        which: Callable[[str], Optional[str]] = shutil.which,
        startup_confirmation_seconds: float = STARTUP_CONFIRMATION_SECONDS,
    ) -> None:
        self._adb = adb_service or AdbService()
        self._app_root = Path(app_root) if app_root else Path(__file__).resolve().parents[2]
        self._on_change = on_change
        self._popen = popen_factory
        self._which = which
        self._startup_confirmation_seconds = max(0.0, startup_confirmation_seconds)
        self._lock = threading.RLock()
        self._sessions: Dict[str, ScreenSession] = {}
        self._shutting_down = False

    def set_on_change(self, callback: Optional[Callable[[ScreenSession], None]]) -> None:
        with self._lock:
            self._on_change = callback

    def resolve_executable(self) -> Path:
        bundled = self._app_root / "tools" / "scrcpy" / "scrcpy.exe"
        if bundled.is_file():
            return bundled
        found = self._which("scrcpy") or self._which("scrcpy.exe")
        if found:
            return Path(found)
        raise ScreenSessionError(
            "No se encontró scrcpy. Coloca scrcpy.exe en "
            "tools/scrcpy/scrcpy.exe o instala scrcpy y agrega su carpeta a PATH."
        )

    def build_command(
        self,
        serial: str,
        preset: ScreenPreset | str = ScreenPreset.MEDIUM,
        *,
        stay_awake: bool = True,
        turn_screen_off: bool = False,
    ) -> List[str]:
        preset_value = self._coerce_preset(preset)
        config = SCREEN_PRESETS[preset_value]
        command = [
            "-s", serial,
            "--max-size", str(config.max_size),
            "--max-fps", str(config.max_fps),
            "--video-bit-rate", config.video_bit_rate,
            "--no-audio",
        ]
        if stay_awake:
            command.append("--stay-awake")
        if turn_screen_off:
            command.append("--turn-screen-off")
        return command

    def start(
        self,
        serial: str,
        preset: ScreenPreset | str = ScreenPreset.MEDIUM,
        *,
        stay_awake: bool = True,
        turn_screen_off: bool = False,
    ) -> ScreenSession:
        target = (serial or "").strip()
        if not target:
            raise ScreenSessionError("Selecciona un dispositivo ADB conectado antes de abrir la pantalla.")
        preset_value = self._coerce_preset(preset)

        with self._lock:
            if self._shutting_down:
                raise ScreenSessionError("La aplicación se está cerrando; no se pueden iniciar sesiones.")
            current = self._sessions.get(target)
            if current and current.state in (ScreenSessionState.STARTING, ScreenSessionState.RUNNING):
                if current.process is not None and current.process.poll() is not None:
                    # The monitor thread may not have observed a manual close yet.
                    current.state = ScreenSessionState.STOPPED
                else:
                    raise ScreenSessionError("Ya hay una sesión de pantalla iniciándose o abierta para este dispositivo.")

            try:
                ready_serial = self._adb.resolve_serial(target)
            except Exception as exc:
                raise ScreenSessionError(f"No se puede abrir la pantalla: {exc}") from exc

            executable = self.resolve_executable()
            command = self.build_command(
                ready_serial,
                preset_value,
                stay_awake=stay_awake,
                turn_screen_off=turn_screen_off,
            )
            session = ScreenSession(serial=ready_serial, preset=preset_value, command=tuple(command))
            self._sessions[ready_serial] = session
            environment = os.environ.copy()
            adb_executable = getattr(self._adb, "adb_executable", None)
            if adb_executable:
                environment["ADB"] = str(adb_executable)
            kwargs = {
                "cwd": str(executable.parent),
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = self._popen([str(executable), *command], **kwargs)
            except Exception as exc:
                session.state = ScreenSessionState.ERROR
                session.error = f"No se pudo iniciar scrcpy: {exc}"
                self._notify(session)
                raise ScreenSessionError(session.error) from exc
            session.process = process
            session.reader_thread = threading.Thread(
                target=self._drain_output,
                args=(session,),
                name=f"scrcpy-output-{ready_serial}",
                daemon=True,
            )
            session.monitor_thread = threading.Thread(
                target=self._monitor_process,
                args=(session,),
                name=f"scrcpy-monitor-{ready_serial}",
                daemon=True,
            )
            session.reader_thread.start()
            session.monitor_thread.start()

        self._notify(session)
        logger.info("Sesión de pantalla iniciada para %s con perfil %s", ready_serial, preset_value.value)
        return session

    def stop(self, serial: str) -> bool:
        target = (serial or "").strip()
        with self._lock:
            session = self._sessions.get(target)
            if not session:
                return False
            session.stop_requested.set()
            process = session.process

        stop_error: Optional[str] = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
                except Exception as exc:
                    stop_error = f"No se pudo cerrar scrcpy: {exc}"
            except Exception as exc:
                logger.debug("No se pudo terminar scrcpy de %s limpiamente", target, exc_info=True)
                stop_error = f"No se pudo cerrar scrcpy: {exc}"

        with self._lock:
            session.state = ScreenSessionState.ERROR if stop_error else ScreenSessionState.STOPPED
            session.error = stop_error
        self._join_session_threads(session)
        self._notify(session)
        return True

    def get_session(self, serial: str) -> Optional[ScreenSession]:
        target = (serial or "").strip()
        with self._lock:
            return self._sessions.get(target)

    def shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True
            serials = list(self._sessions)
        for serial in serials:
            self.stop(serial)

    @staticmethod
    def _coerce_preset(preset: ScreenPreset | str) -> ScreenPreset:
        try:
            return preset if isinstance(preset, ScreenPreset) else ScreenPreset(str(preset).lower())
        except ValueError as exc:
            raise ScreenSessionError(f"Perfil de pantalla desconocido: {preset}") from exc

    def _drain_output(self, session: ScreenSession) -> None:
        process = session.process
        stream = getattr(process, "stdout", None) if process else None
        if stream is None:
            return
        try:
            for raw_line in iter(stream.readline, ""):
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                with self._lock:
                    session.logs.append(line)
                logger.debug("scrcpy[%s]: %s", session.serial, line)
        except (OSError, ValueError):
            logger.debug("Salida de scrcpy terminó para %s", session.serial, exc_info=True)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _monitor_process(self, session: ScreenSession) -> None:
        process = session.process
        if process is None:
            return
        try:
            process.wait(timeout=self._startup_confirmation_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                if session.state == ScreenSessionState.STARTING and not session.stop_requested.is_set():
                    session.state = ScreenSessionState.RUNNING
                    changed = True
                else:
                    changed = False
            if changed:
                self._notify(session)
            try:
                process.wait()
            except Exception:
                logger.debug("Error esperando scrcpy de %s", session.serial, exc_info=True)

        reader = session.reader_thread
        if reader and reader is not threading.current_thread():
            reader.join(timeout=OUTPUT_JOIN_SECONDS)
        return_code = process.poll()
        with self._lock:
            if session.stop_requested.is_set():
                session.state = ScreenSessionState.STOPPED
                session.error = None
            elif session.state == ScreenSessionState.STARTING:
                session.state = ScreenSessionState.ERROR
                session.error = self._process_error(session, early_exit=True)
            elif return_code not in (0, None):
                session.state = ScreenSessionState.ERROR
                session.error = self._process_error(session)
            else:
                session.state = ScreenSessionState.STOPPED
                session.error = None
        self._notify(session)

    @staticmethod
    def _process_error(session: ScreenSession, early_exit: bool = False) -> str:
        code = session.process.returncode if session.process else "desconocido"
        detail = "\n".join(list(session.logs)[-8:]).strip()
        prefix = "scrcpy terminó durante el inicio" if early_exit else "scrcpy terminó con error"
        message = f"{prefix} (código {code})."
        return f"{message}\n{detail}" if detail else message

    def _join_session_threads(self, session: ScreenSession) -> None:
        current = threading.current_thread()
        for thread in (session.reader_thread, session.monitor_thread):
            if thread and thread is not current and thread.is_alive():
                thread.join(timeout=OUTPUT_JOIN_SECONDS)

    def _notify(self, session: ScreenSession) -> None:
        with self._lock:
            callback = self._on_change
        if callback:
            try:
                callback(session)
            except Exception:
                logger.debug("Callback de cambio de sesión falló", exc_info=True)

