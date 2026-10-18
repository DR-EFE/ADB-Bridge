"""Servicio de Respaldo Avanzado (BackupService) para Android.

Permite respaldar categorías multimedia y avanzadas (contactos, SMS, llamadas, calendario,
ajustes, apps, wifi, bluetooth, diccionario y portapapeles) sin necesidad de root.
Genera MANIFEST.json y RESUMEN_BACKUP.json con estados tipados:
OK, EMPTY, DENIED, PARTIAL, ERROR, CANCELLED.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ui_automation.adb_manager import get_adb_executable

logger = logging.getLogger("ui.backup_service")

# Patrones para clasificar fallos en consultas
DENIED_PATTERNS = [
    r"securityexception",
    r"permission\s+denial",
    r"permission denied",
    r"not allowed",
    r"access denied",
    r"unauthorized",
]

TRANSIENT_PATTERNS = [
    r"device offline",
    r"device.*not found",
    r"connection reset",
    r"connection refused",
    r"broken pipe",
    r"protocol fault",
    r"closed",
    r"timeout",
]


def _safe_subprocess_flags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def get_default_backup_dir(device_label: str) -> Path:
    """Devuelve la ruta por defecto: %USERPROFILE%\\AndroidBackup\\<dispositivo>_<fecha>."""
    userprofile = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    safe_name = re.sub(r'[\/\\:\*\?"<>\|]', "_", device_label or "Device")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(userprofile) / "AndroidBackup" / f"{safe_name}_{timestamp}"


@dataclass
class QueryDef:
    name: str
    file_name: str
    command: str


@dataclass
class CategoryDef:
    id: str
    name: str
    description: str
    sub_folder: str
    is_multimedia: bool = False
    queries: List[QueryDef] = field(default_factory=list)
    folders: List[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class QueryResult:
    name: str
    file: str
    command: str
    status: str  # OK | EMPTY | DENIED | ERROR | CANCELLED
    exit_code: int
    message: Optional[str] = None
    bytes_written: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Name": self.name,
            "File": self.file,
            "Command": self.command,
            "Status": self.status,
            "ExitCode": self.exit_code,
            "Message": self.message,
            "BytesWritten": self.bytes_written,
        }


@dataclass
class CategoryResult:
    id: str
    name: str
    status: str  # OK | EMPTY | DENIED | PARTIAL | ERROR | CANCELLED
    directory: str
    query_count: int = 0
    successful: int = 0
    empty: int = 0
    denied: int = 0
    errors: int = 0
    queries: List[QueryResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Id": self.id,
            "Name": self.name,
            "Status": self.status,
            "Directory": self.directory,
            "QueryCount": self.query_count,
            "Successful": self.successful,
            "Empty": self.empty,
            "Denied": self.denied,
            "Errors": self.errors,
            "Queries": [q.to_dict() for q in self.queries],
        }


@dataclass
class BackupProgress:
    status: str  # IDLE | RUNNING | COMPLETED | PARTIAL | FAILED | CANCELLED
    current_category: str = ""
    current_step: str = ""
    categories_done: int = 0
    total_categories: int = 0
    percent: float = 0.0
    destination: str = ""
    error: Optional[str] = None


def get_available_category_definitions() -> List[CategoryDef]:
    """Define todas las categorías disponibles para el backup avanzado."""
    return [
        CategoryDef(
            id="multimedia",
            name="Multimedia (Fotos, Videos, Documentos)",
            description="Copia las carpetas DCIM, Download, Documents, Music y Pictures.",
            sub_folder="Multimedia",
            is_multimedia=True,
            folders=["DCIM", "Download", "Documents", "Music", "Pictures"],
            enabled=True,
        ),
        CategoryDef(
            id="contacts",
            name="Contactos",
            description="Consultas raw a com.android.contacts (teléfonos y contactos).",
            sub_folder="Contacts",
            queries=[
                QueryDef("contacts_raw", "contacts_raw.txt", "content query --uri content://com.android.contacts/data/phones/"),
                QueryDef("contacts_all", "contacts_all.txt", "content query --uri content://com.android.contacts/contacts/"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="sms",
            name="SMS",
            description="Mensajes SMS (todos, bandeja de entrada, enviados, borradores).",
            sub_folder="SMS",
            queries=[
                QueryDef("sms_all", "sms_all.txt", "content query --uri content://sms/"),
                QueryDef("sms_inbox", "sms_inbox.txt", "content query --uri content://sms/inbox"),
                QueryDef("sms_sent", "sms_sent.txt", "content query --uri content://sms/sent"),
                QueryDef("sms_draft", "sms_draft.txt", "content query --uri content://sms/draft"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="calls",
            name="Llamadas",
            description="Historial del registro de llamadas telefónicas.",
            sub_folder="Calls",
            queries=[
                QueryDef("call_log", "call_log.txt", "content query --uri content://call_log/calls"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="calendar",
            name="Calendario",
            description="Eventos, calendarios e instancias registradas.",
            sub_folder="Calendar",
            queries=[
                QueryDef("events", "calendar_events.txt", "content query --uri content://com.android.calendar/events"),
                QueryDef("calendars", "calendar_list.txt", "content query --uri content://com.android.calendar/calendars"),
                QueryDef("instances", "calendar_instances.txt", "content query --uri content://com.android.calendar/instances/when"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="settings",
            name="Ajustes y Sistema",
            description="Ajustes globales, de sistema, seguros y estado de batería/pantalla.",
            sub_folder="SystemSettings",
            queries=[
                QueryDef("global", "settings_global.txt", "settings list global"),
                QueryDef("system", "settings_system.txt", "settings list system"),
                QueryDef("secure", "settings_secure.txt", "settings list secure"),
                QueryDef("display", "display_config.txt", "dumpsys display"),
                QueryDef("battery", "battery_config.txt", "dumpsys battery"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="apps",
            name="Aplicaciones",
            description="Listas de paquetes instalados (todos, usuario, sistema e info).",
            sub_folder="Apps",
            queries=[
                QueryDef("all", "apps_all.txt", "pm list packages"),
                QueryDef("user", "apps_user.txt", "pm list packages -3"),
                QueryDef("system", "apps_system.txt", "pm list packages -s"),
                QueryDef("packages", "packages_info.txt", "dumpsys package"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="wifi",
            name="Wi-Fi",
            description="Redes guardadas y diagnóstico del servicio Wi-Fi.",
            sub_folder="WiFi",
            queries=[
                QueryDef("saved_networks", "wifi_networks.txt", "cmd wifi list-saved-networks"),
                QueryDef("dump", "wifi_dump.txt", "dumpsys wifi"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="bluetooth",
            name="Bluetooth",
            description="Dispositivos vinculados y estado del administrador Bluetooth.",
            sub_folder="Bluetooth",
            queries=[
                QueryDef("paired_devices", "bluetooth_devices.txt", "dumpsys bluetooth_manager"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="dictionary",
            name="Diccionario de Usuario",
            description="Palabras agregadas al diccionario personalizado.",
            sub_folder="Dictionary",
            queries=[
                QueryDef("words", "user_dictionary.txt", "content query --uri content://com.android.providers.userdictionary/words"),
            ],
            enabled=True,
        ),
        CategoryDef(
            id="clipboard",
            name="Portapapeles",
            description="Contenido actual e historial del portapapeles.",
            sub_folder="Clipboard",
            queries=[
                QueryDef("current", "clipboard_current.txt", "service call clipboard 1"),
                QueryDef("history", "clipboard_history.txt", "dumpsys clipboard"),
            ],
            enabled=True,
        ),
    ]


class BackupService:
    """Gestiona la ejecución y generación de respaldos avanzados."""

    def __init__(
        self,
        on_progress: Optional[Callable[[BackupProgress], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._on_progress = on_progress
        self._on_log = on_log
        self._cancel_event = threading.Event()
        self._active_process: Optional[subprocess.Popen] = None
        self._is_running = False
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._is_running

    def cancel(self) -> None:
        """Detiene de inmediato la ejecución del backup conservando lo transferido."""
        logger.info("Cancelación de backup solicitada.")
        self._cancel_event.set()
        with self._lock:
            if self._active_process:
                try:
                    self._active_process.terminate()
                    try:
                        self._active_process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        self._active_process.kill()
                except Exception as exc:
                    logger.debug("Error terminando proceso activo de backup: %s", exc)

    def _notify_progress(self, progress: BackupProgress) -> None:
        if self._on_progress:
            try:
                self._on_progress(progress)
            except Exception as exc:
                logger.debug("Error en callback de progreso de backup: %s", exc)

    def _log(self, msg: str, level: str = "INFO") -> None:
        if self._on_log:
            try:
                self._on_log(msg, level)
            except Exception:
                pass
        logger.info("[Backup] %s", msg)

    def run_backup(
        self,
        serial: str,
        device_label: str,
        destination_dir: Path,
        categories: List[CategoryDef],
        reset_cancel: bool = True,
    ) -> Dict[str, Any]:
        """
        Ejecuta el plan de respaldo secuencialmente.
        Genera MANIFEST.json y RESUMEN_BACKUP.json.
        Devuelve el diccionario con el resumen.
        """
        self._is_running = True
        if reset_cancel:
            self._cancel_event.clear()

        enabled_categories = [c for c in categories if c.enabled]
        total_cats = len(enabled_categories)

        destination_dir.mkdir(parents=True, exist_ok=True)
        adb_exe = get_adb_executable()

        progress = BackupProgress(
            status="RUNNING",
            total_categories=total_cats,
            destination=str(destination_dir.resolve()),
        )
        self._notify_progress(progress)
        self._log(f"Iniciando respaldo en {destination_dir.resolve()} con {total_cats} categorías.", "INFO")

        category_results: List[CategoryResult] = []

        for idx, cat in enumerate(enabled_categories):
            if self._cancel_event.is_set():
                break

            progress.current_category = cat.name
            progress.categories_done = idx
            progress.percent = idx / max(1, total_cats)
            progress.current_step = f"Procesando {cat.name}..."
            self._notify_progress(progress)

            cat_dir = destination_dir / cat.sub_folder
            cat_dir.mkdir(parents=True, exist_ok=True)

            try:
                if cat.is_multimedia:
                    cat_res = self._backup_multimedia(adb_exe, serial, cat, cat_dir, progress)
                else:
                    cat_res = self._backup_queries(adb_exe, serial, cat, cat_dir, progress)
            except Exception as exc:
                # Un fallo de una categoría (por ejemplo, una transferencia
                # ADB agotada) no debe cancelar el respaldo total ni impedir
                # que se escriba el manifiesto de lo que sí se pudo obtener.
                logger.exception("Fallo no controlado en la categoría '%s'.", cat.name)
                cat_res = CategoryResult(
                    id=cat.id,
                    name=cat.name,
                    status="ERROR",
                    directory=str(cat_dir.resolve()),
                    query_count=len(cat.folders) if cat.is_multimedia else len(cat.queries),
                    errors=1,
                    queries=[QueryResult(
                        name=cat.name,
                        file=str(cat_dir.resolve()),
                        command="backup category",
                        status="ERROR",
                        exit_code=-1,
                        message=str(exc),
                    )],
                )

            category_results.append(cat_res)
            self._log(f"Categoría '{cat.name}': {cat_res.status}", "INFO" if cat_res.status == "OK" else "WARN")

        # Determinar estado general
        was_cancelled = self._cancel_event.is_set()
        all_ok = all(r.status == "OK" for r in category_results) and not was_cancelled
        any_partial = any(r.status in ("PARTIAL", "EMPTY", "DENIED") for r in category_results)
        any_error = any(r.status == "ERROR" for r in category_results)

        if was_cancelled:
            overall_status = "CANCELLED" if not category_results else "PARTIAL"
        elif all_ok:
            overall_status = "OK"
        elif any_error:
            overall_status = "PARTIAL" if any(r.status == "OK" for r in category_results) else "ERROR"
        elif any_partial:
            overall_status = "PARTIAL"
        else:
            overall_status = "OK"

        # Generar MANIFEST.json y RESUMEN_BACKUP.json
        manifest_data = {
            "SchemaVersion": 1,
            "GeneratedUtc": datetime.now(timezone.utc).isoformat(),
            "DeviceSerial": serial,
            "DeviceLabel": device_label,
            "OverallStatus": overall_status,
            "Complete": (overall_status == "OK"),
            "Destination": str(destination_dir.resolve()),
            "Categories": [r.to_dict() for r in category_results],
        }

        manifest_path = destination_dir / "MANIFEST.json"
        try:
            manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Error al escribir MANIFEST.json: %s", exc)

        summary_data = {
            "GeneratedUtc": datetime.now(timezone.utc).isoformat(),
            "DeviceSerial": serial,
            "DeviceLabel": device_label,
            "OverallStatus": overall_status,
            "Complete": (overall_status == "OK"),
            "Manifest": str(manifest_path.resolve()),
            "Categories": [
                {"Id": r.id, "Name": r.name, "Status": r.status, "Successful": r.successful, "Errors": r.errors}
                for r in category_results
            ],
        }

        summary_path = destination_dir / "RESUMEN_BACKUP.json"
        try:
            summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Error al escribir RESUMEN_BACKUP.json: %s", exc)

        self._is_running = False
        progress.status = overall_status
        progress.categories_done = len(category_results)
        progress.percent = 1.0 if overall_status == "OK" else (len(category_results) / max(1, total_cats))
        progress.current_step = f"Respaldo finalizado con estado {overall_status}."
        self._notify_progress(progress)

        self._log(f"Respaldo finalizado. Estado: {overall_status}. Manifiesto: {manifest_path}", "INFO")
        return summary_data

    def _run_cmd(
        self,
        adb_exe: str,
        args: List[str],
        timeout: Optional[float] = None,
    ) -> Tuple[int, str, str]:
        def run_once() -> Tuple[int, str, str]:
            proc = subprocess.Popen(
                [adb_exe] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_safe_subprocess_flags(),
            )
            with self._lock:
                self._active_process = proc

            try:
                out, err = proc.communicate(timeout=timeout)
                return proc.returncode, out or "", err or ""
            except subprocess.TimeoutExpired as exc:
                # Un timeout no debe escapar al worker de la UI. Terminamos
                # sólo este comando y lo convertimos en resultado clasificable.
                proc.kill()
                out, err = proc.communicate()
                timeout_text = f"Tiempo agotado tras {timeout} segundos."
                captured_err = exc.stderr or ""
                if isinstance(captured_err, bytes):
                    captured_err = captured_err.decode("utf-8", errors="replace")
                return 124, out or "", "\n".join(
                    part for part in (timeout_text, str(captured_err).strip(), err or "") if part
                )
            finally:
                with self._lock:
                    if self._active_process == proc:
                        self._active_process = None

        ret, out, err = run_once()
        combined = f"{err} {out}".lower()
        # Cuando el endpoint Wi-Fi cae, adb puede conservar el serial como
        # offline. Renovar sólo el enlace TCP y reintentar una vez permite que
        # el backup continúe sin reiniciar el daemon ADB ni duplicar pulls que
        # ya agotaron su tiempo.
        network_serial = len(args) > 1 and ":" in str(args[1])
        transient = ret != 0 and any(re.search(pattern, combined) for pattern in TRANSIENT_PATTERNS)
        if network_serial and transient:
            serial = str(args[1])
            try:
                subprocess.run(
                    [adb_exe, "connect", serial],
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    check=False,
                    creationflags=_safe_subprocess_flags(),
                )
                retry_ret, retry_out, retry_err = run_once()
                return retry_ret, retry_out, retry_err
            except Exception as exc:
                logger.debug("No se pudo renovar ADB Wi-Fi para %s: %s", serial, exc)
        return ret, out, err

    def _backup_multimedia(
        self,
        adb_exe: str,
        serial: str,
        cat: CategoryDef,
        cat_dir: Path,
        progress: BackupProgress,
    ) -> CategoryResult:
        """Copia secuencialmente las carpetas multimedia principales de /sdcard/."""
        queries_res: List[QueryResult] = []
        successful = 0
        errors = 0
        denied = 0
        empty = 0

        for folder_name in cat.folders:
            if self._cancel_event.is_set():
                break

            progress.current_step = f"Copiando multimedia: {folder_name}..."
            self._notify_progress(progress)

            remote_src = f"/sdcard/{folder_name}"
            local_target = cat_dir / folder_name
            local_target.mkdir(parents=True, exist_ok=True)

            ret, out, err = self._run_cmd(
                adb_exe,
                ["-s", serial, "pull", remote_src, str(local_target.resolve())],
                timeout=300.0,
            )

            # Verificar contenido copiado
            has_files = any(local_target.rglob("*")) if local_target.exists() else False

            if self._cancel_event.is_set():
                q_status = "CANCELLED"
            elif ret == 0:
                if has_files:
                    q_status = "OK"
                    successful += 1
                else:
                    q_status = "EMPTY"
                    empty += 1
            else:
                combined_err = f"{err} {out}".lower()
                if any(re.search(pat, combined_err) for pat in DENIED_PATTERNS):
                    q_status = "DENIED"
                    denied += 1
                elif ret == 124 or any(re.search(pat, combined_err) for pat in TRANSIENT_PATTERNS):
                    q_status = "ERROR"
                    errors += 1
                elif not has_files:
                    q_status = "EMPTY"
                    empty += 1
                else:
                    q_status = "PARTIAL"
                    successful += 1

            queries_res.append(
                QueryResult(
                    name=folder_name,
                    file=str(local_target.resolve()),
                    command=f"adb pull {remote_src}",
                    status=q_status,
                    exit_code=ret,
                    message=err.strip() or (None if q_status == "OK" else out.strip()),
                )
            )

        if self._cancel_event.is_set():
            status = "CANCELLED" if successful == 0 else "PARTIAL"
        elif errors > 0 and successful == 0:
            status = "ERROR"
        elif denied > 0 and successful == 0:
            status = "DENIED"
        elif successful == 0 and empty > 0:
            status = "EMPTY"
        elif denied > 0 or errors > 0 or empty > 0:
            status = "PARTIAL"
        else:
            status = "OK"

        return CategoryResult(
            id=cat.id,
            name=cat.name,
            status=status,
            directory=str(cat_dir.resolve()),
            query_count=len(cat.folders),
            successful=successful,
            empty=empty,
            denied=denied,
            errors=errors,
            queries=queries_res,
        )

    def _backup_queries(
        self,
        adb_exe: str,
        serial: str,
        cat: CategoryDef,
        cat_dir: Path,
        progress: BackupProgress,
    ) -> CategoryResult:
        """Ejecuta las consultas shell tipadas y escribe los archivos de texto."""
        queries_res: List[QueryResult] = []
        successful = 0
        empty = 0
        denied = 0
        errors = 0

        for q in cat.queries:
            if self._cancel_event.is_set():
                break

            progress.current_step = f"{cat.name}: ejecutando {q.name}..."
            self._notify_progress(progress)

            out_file = cat_dir / q.file_name

            # Ejecución por argv directo
            cmd_args = ["-s", serial, "shell"] + q.command.split()
            ret, out, err = self._run_cmd(adb_exe, cmd_args, timeout=30.0)

            clean_out = out.strip()
            err_msg = err.strip()
            combined_err = f"{err} {out}".lower()

            if self._cancel_event.is_set():
                q_status = "CANCELLED"
                content_to_write = "[CANCELADO POR EL USUARIO]\n"
            elif any(re.search(pat, combined_err) for pat in DENIED_PATTERNS):
                q_status = "DENIED"
                denied += 1
                content_to_write = f"[PERMISO DENEGADO]\n{err_msg or clean_out}\n"
            elif ret != 0:
                q_status = "ERROR"
                errors += 1
                content_to_write = f"[ERROR AL EJECUTAR]\nCódigo: {ret}\n{err_msg or clean_out}\n"
            elif not clean_out:
                q_status = "EMPTY"
                empty += 1
                content_to_write = ""
            else:
                q_status = "OK"
                successful += 1
                content_to_write = out

            try:
                out_file.write_text(content_to_write, encoding="utf-8", errors="replace")
                bytes_w = out_file.stat().st_size
            except Exception as exc:
                bytes_w = 0
                logger.warning("Error escribiendo archivo %s: %s", out_file, exc)

            queries_res.append(
                QueryResult(
                    name=q.name,
                    file=str(out_file.resolve()),
                    command=q.command,
                    status=q_status,
                    exit_code=ret,
                    message=err_msg if q_status != "OK" else None,
                    bytes_written=bytes_w,
                )
            )

        if self._cancel_event.is_set():
            status = "CANCELLED" if successful == 0 else "PARTIAL"
        elif errors > 0 and successful == 0:
            status = "ERROR"
        elif denied > 0 and successful == 0:
            status = "DENIED"
        elif successful == 0 and empty > 0:
            status = "EMPTY"
        elif denied > 0 or errors > 0 or empty > 0:
            status = "PARTIAL"
        else:
            status = "OK"

        return CategoryResult(
            id=cat.id,
            name=cat.name,
            status=status,
            directory=str(cat_dir.resolve()),
            query_count=len(cat.queries),
            successful=successful,
            empty=empty,
            denied=denied,
            errors=errors,
            queries=queries_res,
        )
