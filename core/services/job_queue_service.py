"""Servicio Centralizado de Trabajos y Cola de Operaciones (JobQueueService).

Características:
- 1 DeviceWorker por serial de dispositivo (estrictamente secuencial por teléfono).
- LocalWorkerPool para procesamiento local (parseo de chats, reportes, Excel, etc.).
- Ejecución paralela real entre múltiples dispositivos conectados.
- Estados formales: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED.
- Reintentos automáticos (hasta 5 intentos) para fallos transitorios con backoff.
- Cancelación cooperativa limpia mediante threading.Event.
- Historial persistente en %USERPROFILE%\\AndroidTools\\jobs_history.json.
- Notificaciones de cambio en tiempo real mediante callbacks para la interfaz gráfica.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from core.infrastructure.database.sqlite import SQLiteStore

logger = logging.getLogger("ui.job_queue_service")

# Patrones para clasificar fallos transitorios susceptibles de reintento
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
    r"temporarily unavailable",
    r"resource temporarily unavailable",
]


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    WHATSAPP_EXPORT_CHAT = "whatsapp_export_chat"
    WHATSAPP_PROCESS = "whatsapp_process"          # parseo local -> JSON/Excel
    BACKUP_CATEGORY = "backup_category"
    FILE_TRANSFER = "file_transfer"
    ADB_COMMAND = "adb_command"
    LOCAL_PROCESS = "local_process"


@dataclass
class JobItem:
    id: str                                    # job_xxxxxxxxxxxx
    job_type: JobType
    title: str
    serial: Optional[str]                      # None = trabajo local
    status: JobStatus = JobStatus.PENDING
    progress_percent: float = 0.0
    current_step: str = ""
    error: Optional[str] = None
    retries_left: int = 5
    max_retries: int = 5
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    pause_event: threading.Event = field(default_factory=threading.Event, repr=False)
    bytes_total: int = 0
    bytes_completed: int = 0
    files_total: int = 0
    files_completed: int = 0
    skip_existing: bool = False
    only_changes: bool = False
    checksums: Dict[str, str] = field(default_factory=dict)
    failed_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa el trabajo para persistencia en JSON."""
        return {
            "id": self.id,
            "job_type": self.job_type.value if isinstance(self.job_type, JobType) else str(self.job_type),
            "title": self.title,
            "serial": self.serial,
            "status": self.status.value if isinstance(self.status, JobStatus) else str(self.status),
            "progress_percent": self.progress_percent,
            "current_step": self.current_step,
            "error": self.error,
            "retries_left": self.retries_left,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "payload": self.payload,
            "result": self.result,
            "bytes_total": self.bytes_total,
            "bytes_completed": self.bytes_completed,
            "files_total": self.files_total,
            "files_completed": self.files_completed,
            "skip_existing": self.skip_existing,
            "only_changes": self.only_changes,
            "checksums": self.checksums,
            "failed_files": self.failed_files,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JobItem:
        """Crea un JobItem a partir de un diccionario persistido."""
        raw_type = data.get("job_type", JobType.LOCAL_PROCESS.value)
        try:
            job_type = JobType(raw_type)
        except ValueError:
            job_type = JobType.LOCAL_PROCESS

        raw_status = data.get("status", JobStatus.PENDING.value)
        try:
            status = JobStatus(raw_status)
        except ValueError:
            status = JobStatus.PENDING

        item = cls(
            id=data.get("id", f"job_{uuid.uuid4().hex[:12]}"),
            job_type=job_type,
            title=data.get("title", "Trabajo sin título"),
            serial=data.get("serial"),
            status=status,
            progress_percent=float(data.get("progress_percent", 0.0)),
            current_step=data.get("current_step", ""),
            error=data.get("error"),
            retries_left=int(data.get("retries_left", 5)),
            max_retries=int(data.get("max_retries", 5)),
            created_at=data.get("created_at", datetime.now().isoformat()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            payload=data.get("payload", {}),
            result=data.get("result"),
            bytes_total=int(data.get("bytes_total", 0)),
            bytes_completed=int(data.get("bytes_completed", 0)),
            files_total=int(data.get("files_total", 0)),
            files_completed=int(data.get("files_completed", 0)),
            skip_existing=bool(data.get("skip_existing", False)),
            only_changes=bool(data.get("only_changes", False)),
            checksums=dict(data.get("checksums", {})),
            failed_files=list(data.get("failed_files", [])),
        )
        return item


def get_default_jobs_history_path() -> Path:
    """Devuelve la ruta canónica para persistir el historial de trabajos."""
    userprofile = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(userprofile) / "AndroidTools" / "jobs_history.json"


def is_error_transient(error_text: str) -> bool:
    """Comprueba si un mensaje de error coincide con fallos transitorios."""
    if not error_text:
        return False
    lower = error_text.lower()
    return any(re.search(pat, lower) for pat in TRANSIENT_ERROR_PATTERNS)


class JobQueueService:
    """
    Servicio de cola centralizado con soporte para workers dedicados por dispositivo
    y pool de workers locales.
    """

    def __init__(
        self,
        history_path: Optional[Path] = None,
        max_local_workers: int = 3,
        auto_persist: bool = True,
        database_path: Optional[Path] = None,
        database: Optional[SQLiteStore] = None,
    ) -> None:
        self._history_path = history_path or get_default_jobs_history_path()
        self._auto_persist = auto_persist
        self._database = database if database is not None else SQLiteStore(database_path)
        self._lock = threading.RLock()

        # Registro en memoria de todos los trabajos
        self._jobs: Dict[str, JobItem] = {}
        # Registrador de handlers/ejecutores por tipo de trabajo
        self._handlers: Dict[JobType, Callable[[JobItem, Callable[[float, str], None]], Any]] = {}

        # Workers por dispositivo (serial -> queue.Queue)
        self._device_queues: Dict[str, queue.Queue[JobItem]] = {}
        self._device_threads: Dict[str, threading.Thread] = {}

        # Worker pool para trabajos locales (sin serial)
        self._local_executor = ThreadPoolPoolWrapper(max_workers=max_local_workers)

        # Callbacks de observación para la UI
        self._listeners: List[Callable[[JobItem], None]] = []

        # Cargar historial existente
        self._load_history()

    # -------------------------------------------------------------------------
    # Registro de Handlers y Listeners
    # -------------------------------------------------------------------------

    def register_handler(
        self,
        job_type: JobType,
        handler: Callable[[JobItem, Callable[[float, str], None]], Any],
    ) -> None:
        """
        Registra la función ejecutora para un tipo de trabajo.
        Firma: handler(job: JobItem, update_progress: Callable[[float, str], None]) -> Any
        """
        with self._lock:
            self._handlers[job_type] = handler

    def add_listener(self, callback: Callable[[JobItem], None]) -> None:
        """Añade un callback que recibe notificaciones ante cualquier actualización de un trabajo."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[JobItem], None]) -> None:
        """Elimina un callback previamente registrado."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self, job: JobItem) -> None:
        """Emite notificación a todos los listeners registrados de forma segura."""
        with self._lock:
            listeners_copy = list(self._listeners)
        for cb in listeners_copy:
            try:
                cb(job)
            except Exception as exc:
                logger.debug("Error en listener de JobQueueService: %s", exc)

    # -------------------------------------------------------------------------
    # API Pública Principal
    # -------------------------------------------------------------------------

    def enqueue(
        self,
        job_type: JobType,
        title: str,
        serial: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
        custom_handler: Optional[Callable[[JobItem, Callable[[float, str], None]], Any]] = None,
        dispatch: bool = True,
    ) -> str:
        """
        Encola un nuevo trabajo.
        Si serial está presente, se despacha a la cola secuencial del dispositivo.
        Si serial es None, se despacha al pool de workers locales.
        Devuelve el job_id generado.
        """
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = JobItem(
            id=job_id,
            job_type=job_type,
            title=title,
            serial=serial.strip() if serial else None,
            status=JobStatus.PENDING,
            retries_left=max_retries,
            max_retries=max_retries,
            payload=payload or {},
            skip_existing=bool((payload or {}).get("skip_existing", False)),
            only_changes=bool((payload or {}).get("only_changes", False)),
        )

        with self._lock:
            self._jobs[job_id] = job
            if custom_handler:
                # Permite registrar un handler ad-hoc para este trabajo específico
                self._handlers[job_id] = custom_handler  # type: ignore

        logger.info("Encolado trabajo [%s] tipo=%s serial=%s: %s", job_id, job_type.value, serial, title)
        self._notify(job)
        self._persist_history()

        # Algunas operaciones ya se ejecutaron fuera de esta cola y solo
        # necesitan quedar registradas en el historial.
        if dispatch:
            self._dispatch(job)
        return job_id

    def finalize(
        self,
        job_id: str,
        status: JobStatus,
        error: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Marca un trabajo previamente registrado sin volver a ejecutarlo."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.status = status
            job.error = error
            job.result = result
            job.progress_percent = 1.0 if status == JobStatus.COMPLETED else 0.0
            job.finished_at = datetime.now().isoformat()
            job.current_step = "Completado" if status == JobStatus.COMPLETED else "Fallido"
        self._notify(job)
        self._persist_history()
        return True

    def cancel(self, job_id: str) -> bool:
        """
        Cancela un trabajo de forma cooperativa.
        Si está en cola (PENDING), pasa a CANCELLED inmediatamente.
        Si está en ejecución (RUNNING), activa stop_event.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status == JobStatus.PENDING:
                job.status = JobStatus.CANCELLED
                job.finished_at = datetime.now().isoformat()
                job.current_step = "Cancelado antes de iniciar"
                logger.info("Trabajo [%s] cancelado mientras estaba en cola.", job_id)
                self._notify(job)
                self._persist_history()
                return True

            if job.status == JobStatus.RUNNING:
                job.stop_event.set()
                job.current_step = "Cancelación solicitada..."
                logger.info("Solicitada cancelación del trabajo activo [%s].", job_id)
                self._notify(job)
                return True

            return False

    def pause(self, job_id: str) -> bool:
        """Pausa un job sin perder su checkpoint; el handler puede cooperar con wait_if_paused."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                return False
            job.pause_event.set()
            job.status = JobStatus.PAUSED
            job.current_step = "Pausado; checkpoint conservado"
        self._notify(job)
        self._persist_history()
        return True

    def resume(self, job_id: str) -> bool:
        """Reanuda un job pausado desde el progreso persistido."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != JobStatus.PAUSED:
                return False
            job.pause_event.clear()
            job.status = JobStatus.PENDING
            job.current_step = "Reanudado desde checkpoint"
        self._notify(job)
        self._persist_history()
        self._dispatch(job)
        return True

    def wait_if_paused(self, job: JobItem, poll_seconds: float = 0.1) -> bool:
        """Bloquea cooperativamente mientras el job está pausado.

        Devuelve False si se solicitó cancelación y True al continuar.
        """
        while job.pause_event.is_set() and not job.stop_event.is_set():
            time.sleep(poll_seconds)
        return not job.stop_event.is_set()

    def retry(self, job_id: str) -> bool:
        """Re-encola manualmente un trabajo que haya fallado o sido cancelado."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                return False

            job.status = JobStatus.PENDING
            job.error = None
            job.current_step = "Re-encolado manualmente"
            job.progress_percent = 0.0
            job.retries_left = job.max_retries
            job.stop_event.clear()
            job.started_at = None
            job.finished_at = None

        logger.info("Trabajo [%s] re-encolado manualmente.", job_id)
        self._notify(job)
        self._persist_history()
        self._dispatch(job)
        return True

    def get_job(self, job_id: str) -> Optional[JobItem]:
        """Obtiene un trabajo por su ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def get_active_jobs(self) -> List[JobItem]:
        """Devuelve todos los trabajos en estado PENDING o RUNNING."""
        with self._lock:
            return [
                j for j in self._jobs.values()
                if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
            ]

    def get_history(self, limit: int = 100) -> List[JobItem]:
        """Devuelve la lista ordenada de trabajos (más recientes primero)."""
        with self._lock:
            sorted_jobs = sorted(
                self._jobs.values(),
                key=lambda j: j.created_at,
                reverse=True,
            )
            return sorted_jobs[:limit]

    def clear_completed(self) -> None:
        """
        Limpia del historial los trabajos terminados (COMPLETED, FAILED, CANCELLED).
        Los trabajos PENDING o RUNNING se conservan intactos.
        """
        with self._lock:
            to_remove = [
                jid for jid, j in self._jobs.items()
                if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
            ]
            for jid in to_remove:
                del self._jobs[jid]

        logger.info("Historial de trabajos limpiado: %d eliminados.", len(to_remove))
        self._persist_history()
        # Notificar cambio general
        dummy = JobItem(id="clear", job_type=JobType.LOCAL_PROCESS, title="Limpieza", serial=None)
        self._notify(dummy)

    # -------------------------------------------------------------------------
    # Despacho y Ejecución por Workers
    # -------------------------------------------------------------------------

    def _dispatch(self, job: JobItem) -> None:
        """Dirige el trabajo al DeviceWorker correspondiente o al LocalWorkerPool."""
        if job.serial:
            # Enrutamiento a la cola dedicada de este dispositivo
            with self._lock:
                if job.serial not in self._device_queues:
                    q: queue.Queue[JobItem] = queue.Queue()
                    self._device_queues[job.serial] = q
                    t = threading.Thread(
                        target=self._device_worker_loop,
                        args=(job.serial, q),
                        name=f"dev-worker-{job.serial}",
                        daemon=True,
                    )
                    self._device_threads[job.serial] = t
                    t.start()
                target_q = self._device_queues[job.serial]

            target_q.put(job)
        else:
            # Enrutamiento al pool local
            self._local_executor.submit(self._execute_job, job)

    def _device_worker_loop(self, serial: str, q: queue.Queue[JobItem]) -> None:
        """Bucle secuencial por dispositivo: procesa estrictamente un trabajo a la vez."""
        logger.info("Iniciado DeviceWorker para serial: %s", serial)
        while True:
            try:
                job = q.get()
                self._execute_job(job)
                q.task_done()
            except Exception as exc:
                logger.error("Error crítico en DeviceWorker (%s): %s", serial, exc, exc_info=True)

    def _execute_job(self, job: JobItem) -> None:
        """Ejecuta un trabajo con soporte de reintentos, cancelaciones y actualización de progreso."""
        with self._lock:
            # Si ya fue cancelado mientras esperaba en cola
            if job.status in (JobStatus.CANCELLED, JobStatus.PAUSED) or job.stop_event.is_set():
                job.status = JobStatus.CANCELLED
                job.finished_at = datetime.now().isoformat()
                self._notify(job)
                self._persist_history()
                return

            job.status = JobStatus.RUNNING
            job.started_at = datetime.now().isoformat()
            job.current_step = "Iniciando ejecución..."
            job.progress_percent = 0.05

        self._notify(job)
        self._persist_history()

        def update_progress(percent: float, step: str) -> None:
            with self._lock:
                job.progress_percent = max(0.0, min(1.0, percent))
                job.current_step = step
            self._notify(job)

        attempt = 1
        max_attempts = job.max_retries + 1

        while attempt <= max_attempts:
            if job.stop_event.is_set():
                with self._lock:
                    job.status = JobStatus.CANCELLED
                    job.finished_at = datetime.now().isoformat()
                    job.current_step = "Cancelado por el usuario"
                self._notify(job)
                self._persist_history()
                return

            try:
                if not self.wait_if_paused(job):
                    raise RuntimeError("Cancelado por el usuario")
                # Obtener el handler
                handler = None
                with self._lock:
                    # 1. Handler ad-hoc por job_id si existe
                    if job.id in self._handlers:  # type: ignore
                        handler = self._handlers[job.id]  # type: ignore
                    # 2. Handler por JobType
                    elif job.job_type in self._handlers:
                        handler = self._handlers[job.job_type]

                if not handler:
                    raise RuntimeError(f"No hay handler registrado para tipo de trabajo: {job.job_type.value}")

                logger.info("Ejecutando [%s] intento %d/%d: %s", job.id, attempt, max_attempts, job.title)
                result_val = handler(job, update_progress)

                # Si fue cancelado cooperativamente durante la ejecución del handler
                if job.stop_event.is_set():
                    with self._lock:
                        job.status = JobStatus.CANCELLED
                        job.finished_at = datetime.now().isoformat()
                        job.current_step = "Cancelado por el usuario"
                    self._notify(job)
                    self._persist_history()
                    return

                # Éxito
                with self._lock:
                    job.status = JobStatus.COMPLETED
                    job.progress_percent = 1.0
                    job.current_step = "Completado exitosamente"
                    job.error = None
                    job.finished_at = datetime.now().isoformat()
                    if isinstance(result_val, dict):
                        job.result = result_val

                logger.info("Trabajo [%s] COMPLETADO con éxito.", job.id)
                self._notify(job)
                self._persist_history()
                return

            except Exception as exc:
                err_msg = str(exc)
                logger.warning("Fallo en trabajo [%s] intento %d/%d: %s", job.id, attempt, max_attempts, err_msg)

                # Si fue una cancelación cooperativa
                if job.stop_event.is_set():
                    with self._lock:
                        job.status = JobStatus.CANCELLED
                        job.finished_at = datetime.now().isoformat()
                        job.current_step = "Cancelado por el usuario"
                    self._notify(job)
                    self._persist_history()
                    return

                # Evaluar si el fallo es transitorio y quedan reintentos
                is_transient = is_error_transient(err_msg)
                job.retries_left = max(0, max_attempts - attempt - 1)

                if is_transient and job.retries_left > 0 and not job.stop_event.is_set():
                    backoff = min(10.0, 1.5 ** attempt)
                    update_progress(
                        job.progress_percent,
                        f"Fallo transitorio ({err_msg}). Reintentando en {backoff:.1f}s ({job.retries_left} restantes)...",
                    )
                    time.sleep(backoff)
                    attempt += 1
                    continue

                # Fallo permanente o reintentos agotados
                with self._lock:
                    job.status = JobStatus.FAILED
                    job.error = err_msg
                    job.current_step = f"Falló: {err_msg}"
                    job.finished_at = datetime.now().isoformat()

                logger.error("Trabajo [%s] MARCADO COMO FALLIDO: %s", job.id, err_msg)
                self._notify(job)
                self._persist_history()
                return

    # -------------------------------------------------------------------------
    # Persistencia en Disco
    # -------------------------------------------------------------------------

    def _persist_history(self) -> None:
        """Guarda de forma atómica el historial en JSON."""
        try:
            with self._lock:
                data = [job.to_dict() for job in self._jobs.values()]

            if self._database:
                for job_data in data:
                    self._database.save_job(job_data)

            if not self._auto_persist:
                return

            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._history_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(self._history_path)
        except Exception as exc:
            logger.debug("Error persistiendo jobs_history.json: %s", exc)

    def _load_history(self) -> None:
        """Carga el historial guardado en JSON al iniciar."""
        try:
            # SQLite es la fuente de verdad; el JSON solo permite migrar
            # instalaciones anteriores o exportaciones antiguas.
            raw_list = self._database.list_jobs() if self._database else []
            if not raw_list and self._history_path.is_file():
                content = self._history_path.read_text(encoding="utf-8")
                raw_list = json.loads(content)
            if isinstance(raw_list, list):
                with self._lock:
                    for item_data in raw_list:
                        job = JobItem.from_dict(item_data)
                        # Si quedó en PENDING o RUNNING tras un cierre previo anormal, marcar FAILED
                        if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                            job.status = JobStatus.FAILED
                            job.error = "Interrumpido por reinicio de la aplicación"
                            job.finished_at = job.finished_at or datetime.now().isoformat()
                        self._jobs[job.id] = job
                logger.info("Cargados %d trabajos del historial previo.", len(self._jobs))
        except Exception as exc:
            logger.warning("No se pudo cargar historial previo de jobs: %s", exc)

    def shutdown(self) -> None:
        """Cierra ordenadamente los pools y cancela los trabajos en ejecución."""
        logger.info("Cerrando JobQueueService...")
        with self._lock:
            for job in self._jobs.values():
                if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                    job.stop_event.set()
        self._local_executor.shutdown(wait=False)
        self._persist_history()


class ThreadPoolPoolWrapper:
    """Wrapper delgado sobre ThreadPoolExecutor."""

    def __init__(self, max_workers: int = 3):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="local-worker")

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return self._executor.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
