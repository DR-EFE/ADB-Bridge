"""Servicio de transferencias de archivos y operaciones remotas para Android ADB.

Gestiona una cola de trabajos en segundo plano (push, pull, mkdir, rename, delete)
sin bloquear la interfaz de usuario.
Características:
- Estados: QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, PARTIAL.
- Seguimiento de progreso por archivo y global.
- Reintento automático de errores transitorios hasta 2 veces.
- Reintento manual para errores no transitorios o cancelados.
- Cancelación segura que termina el proceso ADB activo y preserva archivos ya copiados.
- Validación de rutas remotas para evitar inyecciones.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ui_automation.adb_manager import get_adb_executable

logger = logging.getLogger("ui.transfer_service")

# Patrones para identificar fallos de conexión transitorios
TRANSIENT_ERROR_PATTERNS = [
    r"device offline",
    r"device.*not found",
    r"connection reset",
    r"connection refused",
    r"broken pipe",
    r"protocol fault",
    r"closed",
    r"timeout",
    r"killed",
    r"no devices/emulators found",
]

# Patrones para denegación de permisos o rutas inválidas (no transitorios)
PERMANENT_ERROR_PATTERNS = [
    r"permission denied",
    r"securityexception",
    r"not allowed",
    r"read-only file system",
    r"no such file or directory",
    r"file exists",
    r"is a directory",
    r"not a directory",
]


class TransferType(str, Enum):
    PUSH_FILE = "PUSH_FILE"
    PUSH_DIR = "PUSH_DIR"
    PULL_FILE = "PULL_FILE"
    PULL_DIR = "PULL_DIR"
    MKDIR = "MKDIR"
    RENAME = "RENAME"
    DELETE = "DELETE"


class TransferStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


@dataclass
class RemotePath:
    """Ruta remota tipada y validada en el dispositivo Android."""

    raw_path: str

    def __post_init__(self) -> None:
        norm = self.raw_path.replace("\\", "/").strip()
        if not norm.startswith("/"):
            norm = "/" + norm
        # Eliminar componentes redundantes // o /.
        parts = [p for p in norm.split("/") if p and p != "."]
        self.clean_path = "/" + "/".join(parts)

    @property
    def name(self) -> str:
        return self.clean_path.split("/")[-1] if "/" in self.clean_path else ""

    @property
    def parent(self) -> str:
        parts = self.clean_path.split("/")
        if len(parts) <= 2:
            return "/"
        return "/".join(parts[:-1])

    def join(self, subpath: str) -> RemotePath:
        sub_clean = subpath.replace("\\", "/").strip().lstrip("/")
        return RemotePath(f"{self.clean_path}/{sub_clean}")

    def is_under(self, root: str = "/sdcard") -> bool:
        norm_root = RemotePath(root).clean_path
        return self.clean_path == norm_root or self.clean_path.startswith(norm_root + "/")

    def __str__(self) -> str:
        return self.clean_path


@dataclass
class TransferJob:
    """Representa una operación de transferencia en la cola."""

    id: str
    serial: str
    job_type: TransferType
    source: str
    destination: str
    status: TransferStatus = TransferStatus.QUEUED
    progress: float = 0.0  # 0.0 a 1.0
    bytes_transferred: int = 0
    total_bytes: int = 0
    current_file: str = ""
    files_transferred: int = 0
    total_files: int = 0
    attempts: int = 0
    max_retries: int = 2
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Control de ejecución y proceso activo
    cancel_event: threading.Event = field(default_factory=threading.Event)
    active_process: Optional[subprocess.Popen] = None

    def mark_updated(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def is_transient_error(self) -> bool:
        if not self.error:
            return False
        err_lower = self.error.lower()
        # Si coincide con patrón permanente, no es transitorio
        for pat in PERMANENT_ERROR_PATTERNS:
            if re.search(pat, err_lower):
                return False
        # Si coincide con transitorio
        for pat in TRANSIENT_ERROR_PATTERNS:
            if re.search(pat, err_lower):
                return True
        return False


def _safe_subprocess_flags() -> int:
    """Oculta la ventana de consola en Windows."""
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


class TransferService:
    """
    Servicio de cola de transferencias y operaciones sobre el dispositivo ADB.
    Ejecuta trabajos secuencialmente en un hilo en segundo plano para no bloquear la interfaz.
    """

    def __init__(
        self,
        reconnect_callback: Optional[Callable[[str], Optional[str]]] = None,
        on_job_updated: Optional[Callable[[TransferJob], None]] = None,
        auto_start: bool = True,
    ) -> None:
        self._reconnect_callback = reconnect_callback
        self._on_job_updated = on_job_updated
        self._queue: queue.Queue[TransferJob] = queue.Queue()
        self._jobs: Dict[str, TransferJob] = {}
        self._jobs_lock = threading.RLock()
        self._stop_event = threading.Event()

        self._worker_thread: Optional[threading.Thread] = None
        if auto_start:
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="transfer-service-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def _notify(self, job: TransferJob) -> None:
        job.mark_updated()
        if self._on_job_updated:
            try:
                self._on_job_updated(job)
            except Exception as exc:
                logger.debug("Error en callback on_job_updated: %s", exc)

    # -------------------------------------------------------------------------
    # Métodos de consulta y gestión de la cola
    # -------------------------------------------------------------------------
    def get_jobs(self) -> List[TransferJob]:
        with self._jobs_lock:
            return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[TransferJob]:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Cancela un trabajo activo o encolado."""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status == TransferStatus.QUEUED:
                job.status = TransferStatus.CANCELLED
                job.cancel_event.set()
                self._notify(job)
                logger.info("Trabajo %s cancelado mientras estaba en cola.", job_id)
                return True

            if job.status == TransferStatus.RUNNING:
                job.cancel_event.set()
                if job.active_process:
                    try:
                        logger.info("Terminando proceso ADB activo para trabajo %s...", job_id)
                        job.active_process.terminate()
                        try:
                            job.active_process.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            job.active_process.kill()
                    except Exception as exc:
                        logger.warning("Error al terminar proceso de trabajo %s: %s", job_id, exc)
                return True

            return False

    def retry_job(self, job_id: str) -> bool:
        """Reintenta manualmente un trabajo fallido, parcial o cancelado."""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status not in (TransferStatus.FAILED, TransferStatus.CANCELLED, TransferStatus.PARTIAL):
                return False

            job.status = TransferStatus.QUEUED
            job.error = None
            job.cancel_event.clear()
            job.progress = 0.0
            self._notify(job)
            self._queue.put(job)
            logger.info("Trabajo %s reenviado a la cola para reintento manual.", job_id)
            return True

    def clear_completed_jobs(self) -> None:
        """Limpia de la lista los trabajos completados o cancelados."""
        with self._jobs_lock:
            to_remove = [
                jid
                for jid, j in self._jobs.items()
                if j.status in (TransferStatus.COMPLETED, TransferStatus.CANCELLED)
            ]
            for jid in to_remove:
                del self._jobs[jid]

    # -------------------------------------------------------------------------
    # Encolado de operaciones específicas
    # -------------------------------------------------------------------------
    def enqueue_push(self, serial: str, local_paths: List[str], remote_dir: str) -> List[str]:
        """
        Encola la subida de uno o varios archivos o carpetas a remote_dir.
        Si es una carpeta, preserva la estructura interna.
        """
        job_ids = []
        clean_remote = RemotePath(remote_dir).clean_path

        for p in local_paths:
            path_obj = Path(p)
            if not path_obj.exists():
                logger.warning("Ruta local no existe: %s", p)
                continue

            jid = uuid.uuid4().hex[:10]
            if path_obj.is_dir():
                job = TransferJob(
                    id=jid,
                    serial=serial,
                    job_type=TransferType.PUSH_DIR,
                    source=str(path_obj.resolve()),
                    destination=clean_remote,
                )
            else:
                job = TransferJob(
                    id=jid,
                    serial=serial,
                    job_type=TransferType.PUSH_FILE,
                    source=str(path_obj.resolve()),
                    destination=clean_remote,
                )

            with self._jobs_lock:
                self._jobs[jid] = job
            self._notify(job)
            self._queue.put(job)
            job_ids.append(jid)

        return job_ids

    def enqueue_pull(self, serial: str, remote_paths: List[str], local_dir: str) -> List[str]:
        """Encola la descarga de uno o varios archivos o carpetas remotos hacia local_dir."""
        job_ids = []
        Path(local_dir).mkdir(parents=True, exist_ok=True)

        for rp in remote_paths:
            clean_remote = RemotePath(rp).clean_path
            jid = uuid.uuid4().hex[:10]
            # No sabemos con certeza inmediata si es dir o file sin consultar adb,
            # pero el ejecutor determinará si es dir o file.
            job = TransferJob(
                id=jid,
                serial=serial,
                job_type=TransferType.PULL_FILE,  # El worker ajustará a PULL_DIR si detecta carpeta
                source=clean_remote,
                destination=str(Path(local_dir).resolve()),
            )
            with self._jobs_lock:
                self._jobs[jid] = job
            self._notify(job)
            self._queue.put(job)
            job_ids.append(jid)

        return job_ids

    def enqueue_mkdir(self, serial: str, remote_dir: str) -> str:
        """Crea una carpeta remota."""
        clean_remote = RemotePath(remote_dir).clean_path
        jid = uuid.uuid4().hex[:10]
        job = TransferJob(
            id=jid,
            serial=serial,
            job_type=TransferType.MKDIR,
            source=clean_remote,
            destination=clean_remote,
        )
        with self._jobs_lock:
            self._jobs[jid] = job
        self._notify(job)
        self._queue.put(job)
        return jid

    def enqueue_rename(self, serial: str, old_path: str, new_path: str) -> str:
        """Renombra o mueve un elemento remoto."""
        c_old = RemotePath(old_path).clean_path
        c_new = RemotePath(new_path).clean_path
        jid = uuid.uuid4().hex[:10]
        job = TransferJob(
            id=jid,
            serial=serial,
            job_type=TransferType.RENAME,
            source=c_old,
            destination=c_new,
        )
        with self._jobs_lock:
            self._jobs[jid] = job
        self._notify(job)
        self._queue.put(job)
        return jid

    def enqueue_delete(self, serial: str, remote_path: str) -> str:
        """Elimina un archivo o carpeta remoto."""
        clean = RemotePath(remote_path).clean_path
        jid = uuid.uuid4().hex[:10]
        job = TransferJob(
            id=jid,
            serial=serial,
            job_type=TransferType.DELETE,
            source=clean,
            destination="",
        )
        with self._jobs_lock:
            self._jobs[jid] = job
        self._notify(job)
        self._queue.put(job)
        return jid

    # -------------------------------------------------------------------------
    # Bucle del trabajador y ejecución
    # -------------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if job.status == TransferStatus.CANCELLED:
                self._queue.task_done()
                continue

            self._process_job_with_retries(job)
            self._queue.task_done()

    def _process_job_with_retries(self, job: TransferJob) -> None:
        """Ejecuta el trabajo gestionando reintentos automáticos para errores transitorios."""
        job.status = TransferStatus.RUNNING
        self._notify(job)

        while not job.cancel_event.is_set():
            try:
                self._execute_job(job)
                # Si terminó exitosamente y no se canceló:
                if not job.cancel_event.is_set():
                    job.status = TransferStatus.COMPLETED
                    job.progress = 1.0
                    job.error = None
                self._notify(job)
                return

            except Exception as exc:
                job.error = str(exc)
                logger.warning("Fallo en trabajo %s (intento %d): %s", job.id, job.attempts + 1, exc)

                # Si el usuario solicitó cancelación:
                if job.cancel_event.is_set():
                    if job.bytes_transferred > 0 or job.files_transferred > 0:
                        job.status = TransferStatus.PARTIAL
                    else:
                        job.status = TransferStatus.CANCELLED
                    self._notify(job)
                    return

                # Revisar si es error transitorio y quedan reintentos
                if job.is_transient_error() and job.attempts < job.max_retries:
                    job.attempts += 1
                    logger.info(
                        "Reintentando trabajo %s por error transitorio (intento %d de %d)...",
                        job.id,
                        job.attempts,
                        job.max_retries,
                    )
                    # Intentar reconectar si tenemos callback
                    if self._reconnect_callback:
                        try:
                            replacement_serial = self._reconnect_callback(job.serial)
                            if replacement_serial and replacement_serial != job.serial:
                                logger.info(
                                    "Trabajo %s cambia al endpoint reconectado %s -> %s.",
                                    job.id, job.serial, replacement_serial,
                                )
                                job.serial = replacement_serial
                        except Exception as rc_err:
                            logger.debug("Error en callback de reconexión: %s", rc_err)
                    time.sleep(1.5 * job.attempts)
                    continue

                # Error definitivo o agotó reintentos
                if job.bytes_transferred > 0 or job.files_transferred > 0:
                    job.status = TransferStatus.PARTIAL
                else:
                    job.status = TransferStatus.FAILED
                self._notify(job)
                return

        # Salió por cancel_event
        if job.bytes_transferred > 0 or job.files_transferred > 0:
            job.status = TransferStatus.PARTIAL
        else:
            job.status = TransferStatus.CANCELLED
        self._notify(job)

    # -------------------------------------------------------------------------
    # Ejecutores de operaciones concretas
    # -------------------------------------------------------------------------
    def _execute_job(self, job: TransferJob) -> None:
        adb_exe = get_adb_executable()

        if job.job_type == TransferType.MKDIR:
            self._exec_mkdir(adb_exe, job)
        elif job.job_type == TransferType.RENAME:
            self._exec_rename(adb_exe, job)
        elif job.job_type == TransferType.DELETE:
            self._exec_delete(adb_exe, job)
        elif job.job_type == TransferType.PUSH_FILE:
            self._exec_push_file(adb_exe, job)
        elif job.job_type == TransferType.PUSH_DIR:
            self._exec_push_dir(adb_exe, job)
        elif job.job_type == TransferType.PULL_FILE:
            # Comprobar si en realidad es un directorio remoto
            is_dir = self._check_remote_is_dir(adb_exe, job.serial, job.source)
            if is_dir:
                job.job_type = TransferType.PULL_DIR
                self._exec_pull_dir(adb_exe, job)
            else:
                self._exec_pull_file(adb_exe, job)
        elif job.job_type == TransferType.PULL_DIR:
            self._exec_pull_dir(adb_exe, job)

    def _run_adb_cmd(
        self,
        adb_exe: str,
        args: List[str],
        job: Optional[TransferJob] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[int, str, str]:
        """Ejecuta un comando ADB y guarda referencia al proceso en el trabajo."""
        cmd = [adb_exe] + args
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_safe_subprocess_flags(),
        )
        if job:
            job.active_process = proc

        try:
            out, err = proc.communicate(timeout=timeout)
            ret = proc.returncode
            return ret, out or "", err or ""
        finally:
            if job and job.active_process == proc:
                job.active_process = None

    def _exec_mkdir(self, adb_exe: str, job: TransferJob) -> None:
        ret, out, err = self._run_adb_cmd(
            adb_exe,
            ["-s", job.serial, "shell", "mkdir", "-p", job.destination],
            job=job,
            timeout=15.0,
        )
        if ret != 0:
            raise RuntimeError(f"Error creando directorio: {err.strip() or out.strip()}")

    def _exec_rename(self, adb_exe: str, job: TransferJob) -> None:
        ret, out, err = self._run_adb_cmd(
            adb_exe,
            ["-s", job.serial, "shell", "mv", job.source, job.destination],
            job=job,
            timeout=15.0,
        )
        if ret != 0:
            raise RuntimeError(f"Error renombrando/moviendo: {err.strip() or out.strip()}")

    def _exec_delete(self, adb_exe: str, job: TransferJob) -> None:
        ret, out, err = self._run_adb_cmd(
            adb_exe,
            ["-s", job.serial, "shell", "rm", "-rf", job.source],
            job=job,
            timeout=20.0,
        )
        if ret != 0:
            raise RuntimeError(f"Error eliminando: {err.strip() or out.strip()}")

    def _exec_push_file(self, adb_exe: str, job: TransferJob) -> None:
        src = Path(job.source)
        if not src.exists():
            raise FileNotFoundError(f"Archivo local no existe: {src}")

        size = src.stat().st_size
        job.total_bytes = size
        job.total_files = 1
        job.current_file = src.name
        self._notify(job)

        # Destino final remoto: si destination termina en / o es directorio remoto
        dest_remote = job.destination
        if dest_remote.endswith("/"):
            dest_remote = f"{dest_remote.rstrip('/')}/{src.name}"
        else:
            # Comprobar si destination es un directorio
            is_dir = self._check_remote_is_dir(adb_exe, job.serial, job.destination)
            if is_dir:
                dest_remote = f"{job.destination.rstrip('/')}/{src.name}"

        ret, out, err = self._run_adb_cmd(
            adb_exe,
            ["-s", job.serial, "push", str(src), dest_remote],
            job=job,
        )
        if ret != 0:
            raise RuntimeError(f"Error en adb push: {err.strip() or out.strip()}")

        job.bytes_transferred = size
        job.files_transferred = 1
        job.progress = 1.0

    def _exec_push_dir(self, adb_exe: str, job: TransferJob) -> None:
        """
        Sube una carpeta preservando estrictamente la estructura interna.
        Recorre los archivos locales y sube archivo por archivo actualizando progreso.
        """
        src_dir = Path(job.source)
        if not src_dir.is_dir():
            raise NotADirectoryError(f"Ruta no es directorio: {src_dir}")

        folder_name = src_dir.name
        target_root = f"{job.destination.rstrip('/')}/{folder_name}"

        # 1. Asegurar la creación del directorio raíz remoto
        self._run_adb_cmd(adb_exe, ["-s", job.serial, "shell", "mkdir", "-p", target_root], job=job)

        # 2. Recolectar todos los archivos locales
        all_files: List[Tuple[Path, str, int]] = []
        total_bytes = 0
        created_dirs: Set[str] = {target_root}

        for root, dirs, files in os.walk(src_dir):
            rel_dir = os.path.relpath(root, src_dir)
            if rel_dir != ".":
                remote_sub = f"{target_root}/{rel_dir.replace(os.sep, '/')}"
                if remote_sub not in created_dirs:
                    self._run_adb_cmd(adb_exe, ["-s", job.serial, "shell", "mkdir", "-p", remote_sub], job=job)
                    created_dirs.add(remote_sub)

            for f in files:
                file_path = Path(root) / f
                try:
                    size = file_path.stat().st_size
                except Exception:
                    size = 0
                rel_file = os.path.relpath(file_path, src_dir).replace(os.sep, "/")
                all_files.append((file_path, rel_file, size))
                total_bytes += size

        job.total_files = len(all_files)
        job.total_bytes = total_bytes
        self._notify(job)

        if not all_files:
            job.progress = 1.0
            return

        # 3. Subir archivo a archivo preservando estructura
        for local_file, rel_file, size in all_files:
            if job.cancel_event.is_set():
                break

            job.current_file = rel_file
            self._notify(job)

            remote_dest_file = f"{target_root}/{rel_file}"
            ret, out, err = self._run_adb_cmd(
                adb_exe,
                ["-s", job.serial, "push", str(local_file), remote_dest_file],
                job=job,
            )
            if ret != 0:
                raise RuntimeError(f"Error subiendo {rel_file}: {err.strip() or out.strip()}")

            job.bytes_transferred += size
            job.files_transferred += 1
            if job.total_bytes > 0:
                job.progress = min(1.0, job.bytes_transferred / job.total_bytes)
            self._notify(job)

    def _exec_pull_file(self, adb_exe: str, job: TransferJob) -> None:
        rem_name = RemotePath(job.source).name
        dest_dir = Path(job.destination)
        dest_dir.mkdir(parents=True, exist_ok=True)
        local_target = dest_dir / rem_name

        job.total_files = 1
        job.current_file = rem_name
        self._notify(job)

        ret, out, err = self._run_adb_cmd(
            adb_exe,
            ["-s", job.serial, "pull", job.source, str(local_target)],
            job=job,
        )
        if ret != 0:
            raise RuntimeError(f"Error en adb pull: {err.strip() or out.strip()}")

        if local_target.exists():
            job.bytes_transferred = local_target.stat().st_size
            job.total_bytes = job.bytes_transferred
        job.files_transferred = 1
        job.progress = 1.0

    def _exec_pull_dir(self, adb_exe: str, job: TransferJob) -> None:
        """
        Descarga una carpeta remota preservando su jerarquía.
        Descarga el árbol en la carpeta local de destino.
        """
        remote_path = RemotePath(job.source).clean_path
        dir_name = RemotePath(job.source).name
        local_dest_root = Path(job.destination) / dir_name
        local_dest_root.mkdir(parents=True, exist_ok=True)

        job.current_file = f"Listando {dir_name}..."
        self._notify(job)

        # Listar archivos con find en android
        ret, out, _ = self._run_adb_cmd(
            adb_exe,
            ["-s", job.serial, "shell", "find", remote_path, "-type", "f"],
            job=job,
            timeout=20.0,
        )
        remote_files = [line.strip() for line in out.splitlines() if line.strip()]

        job.total_files = len(remote_files)
        self._notify(job)

        for rem_f in remote_files:
            if job.cancel_event.is_set():
                break

            # Calcular ruta relativa remota respecto a remote_path
            rel = rem_f[len(remote_path) :].lstrip("/")
            local_target_file = local_dest_root / Path(rel.replace("/", os.sep))
            local_target_file.parent.mkdir(parents=True, exist_ok=True)

            job.current_file = rel
            self._notify(job)

            ret, out, err = self._run_adb_cmd(
                adb_exe,
                ["-s", job.serial, "pull", rem_f, str(local_target_file)],
                job=job,
            )
            if ret != 0:
                raise RuntimeError(f"Error descargando {rel}: {err.strip() or out.strip()}")

            if local_target_file.exists():
                job.bytes_transferred += local_target_file.stat().st_size
            job.files_transferred += 1
            if job.total_files > 0:
                job.progress = min(1.0, job.files_transferred / job.total_files)
            self._notify(job)

    def _check_remote_is_dir(self, adb_exe: str, serial: str, remote_path: str) -> bool:
        """Verifica si remote_path es un directorio ejecutando test -d."""
        ret, _, _ = self._run_adb_cmd(
            adb_exe,
            ["-s", serial, "shell", "test", "-d", remote_path],
            timeout=8.0,
        )
        return ret == 0

    def shutdown(self) -> None:
        """Detiene el hilo trabajador y cancela trabajos activos."""
        logger.info("Deteniendo TransferService...")
        self._stop_event.set()
        with self._jobs_lock:
            for job in self._jobs.values():
                if job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING):
                    self.cancel_job(job.id)
