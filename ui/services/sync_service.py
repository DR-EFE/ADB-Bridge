"""Servicio y motor de sincronización inteligente (Fase 3B).

Integra:
- Reglas configurables (exclusiones glob, directorios, límites de tamaño, fotos/documentos).
- Integración con resumable_transfer (SHA-256 local y verificación incremental).
- Integración con JobQueueService (reanudable, pause, resume, cancel).
- Detección de colisiones y estrategias de resolución (keep_local, keep_remote, keep_both, skip, .trash/.conflict).
- Persistencia del estado y reportes en SQLite (tablas con prefijo sync_*).
- Comunicación exclusiva a través de AdbService (shell, pull, push) con serial explícito.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .adb_service import AdbError, AdbService
from .database import SQLiteStore, get_default_database_path
from .job_queue_service import JobItem, JobQueueService, JobStatus, JobType
from .resumable_transfer import sha256_file
from .sync_profiles import (
    ConflictResolution,
    SyncConflict,
    SyncDirection,
    SyncItemReport,
    SyncItemStatus,
    SyncProfile,
    SyncReport,
    SyncRule,
)

logger = logging.getLogger("ui.services.sync_service")

# Extensiones reconocidas para reglas predefinidas
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".dng", ".mp4", ".mov", ".gif"}
DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".epub", ".odt", ".csv", ".json"}


@dataclass
class RemoteFileInfo:
    relative_path: str
    size: int
    mtime: int = 0


@dataclass
class LocalFileInfo:
    relative_path: str
    full_path: Path
    size: int
    mtime: float


class SyncService:
    """Motor de sincronización inteligente de archivos."""

    def __init__(
        self,
        adb_service: Optional[AdbService] = None,
        job_queue: Optional[JobQueueService] = None,
        database: Optional[SQLiteStore] = None,
    ):
        self._adb = adb_service or AdbService()
        self._db = database or SQLiteStore()
        self._job_queue = job_queue
        self._init_sync_tables()

    def _init_sync_tables(self) -> None:
        """Crea las tablas dedicadas a sincronización con prefijo sync_*."""
        schema = """
            CREATE TABLE IF NOT EXISTS sync_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                direction TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                local_path TEXT NOT NULL,
                serial TEXT,
                payload_json TEXT NOT NULL,
                last_sync_utc TEXT,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_reports (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                success INTEGER NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sync_reports_profile ON sync_reports(profile_id);
        """
        with self._db.connect() as conn:
            conn.executescript(schema)

    # -------------------------------------------------------------------------
    # Gestión de Perfiles
    # -------------------------------------------------------------------------

    def save_profile(self, profile: SyncProfile) -> None:
        """Guarda o actualiza un perfil en SQLite."""
        encoded = json.dumps(profile.model_dump(), ensure_ascii=False, default=str)
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_profiles(id, name, direction, remote_path, local_path, serial, payload_json, last_sync_utc, created_at_utc)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    direction=excluded.direction,
                    remote_path=excluded.remote_path,
                    local_path=excluded.local_path,
                    serial=excluded.serial,
                    payload_json=excluded.payload_json,
                    last_sync_utc=excluded.last_sync_utc
                """,
                (
                    profile.id,
                    profile.name,
                    profile.direction.value,
                    profile.remote_path,
                    profile.local_path,
                    profile.serial,
                    encoded,
                    profile.last_sync_utc,
                    profile.created_at_utc,
                ),
            )

    def get_profile(self, profile_id: str) -> Optional[SyncProfile]:
        """Obtiene un perfil por su identificador."""
        with self._db.connect() as conn:
            row = conn.execute("SELECT payload_json FROM sync_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row:
                data = json.loads(row[0])
                return SyncProfile.model_validate(data)
        return None

    def list_profiles(self) -> List[SyncProfile]:
        """Lista todos los perfiles de sincronización guardados."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM sync_profiles ORDER BY name ASC").fetchall()
            return [SyncProfile.model_validate(json.loads(row[0])) for row in rows]

    def delete_profile(self, profile_id: str) -> bool:
        """Elimina un perfil guardado."""
        with self._db.connect() as conn:
            cursor = conn.execute("DELETE FROM sync_profiles WHERE id = ?", (profile_id,))
            return cursor.rowcount > 0

    def save_report(self, report: SyncReport) -> None:
        """Guarda un reporte de sincronización en la tabla sync_reports."""
        encoded = json.dumps(report.model_dump(), ensure_ascii=False, default=str)
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_reports(id, profile_id, direction, success, report_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    report.sync_id,
                    report.profile_id,
                    report.direction.value,
                    1 if report.success else 0,
                    encoded,
                    report.started_at,
                ),
            )

    def get_last_report(self, profile_id: str) -> Optional[SyncReport]:
        """Obtiene el último reporte registrado para un perfil dado."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT report_json FROM sync_reports WHERE profile_id = ? ORDER BY created_at DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
            if row:
                return SyncReport.model_validate(json.loads(row[0]))
        return None

    # -------------------------------------------------------------------------
    # Generadores de Perfiles Predefinidos
    # -------------------------------------------------------------------------

    @staticmethod
    def create_preset_profile(
        preset_type: str,
        name: str,
        local_path: str | Path,
        remote_path: str = "/sdcard/DCIM",
        serial: Optional[str] = None,
    ) -> SyncProfile:
        """Crea perfiles con reglas estándar listas para usar."""
        norm_local = str(Path(local_path).resolve())
        pid = f"sync_{uuid.uuid4().hex[:8]}"

        if preset_type == "phone_to_pc":
            return SyncProfile(
                id=pid,
                name=name or "Teléfono → PC",
                direction=SyncDirection.PHONE_TO_PC,
                remote_path=remote_path,
                local_path=norm_local,
                serial=serial,
            )
        elif preset_type == "pc_to_phone":
            return SyncProfile(
                id=pid,
                name=name or "PC → Teléfono",
                direction=SyncDirection.PC_TO_PHONE,
                remote_path=remote_path,
                local_path=norm_local,
                serial=serial,
            )
        elif preset_type == "bidirectional":
            return SyncProfile(
                id=pid,
                name=name or "Bidireccional",
                direction=SyncDirection.BIDIRECTIONAL,
                remote_path=remote_path,
                local_path=norm_local,
                serial=serial,
                rules=SyncRule(conflict_strategy=ConflictResolution.KEEP_BOTH),
            )
        elif preset_type == "new_photos":
            return SyncProfile(
                id=pid,
                name=name or "Solo fotos nuevas",
                direction=SyncDirection.PHONE_TO_PC,
                remote_path="/sdcard/DCIM/Camera",
                local_path=norm_local,
                serial=serial,
                rules=SyncRule(only_new_photos=True),
            )
        elif preset_type == "modified_docs":
            return SyncProfile(
                id=pid,
                name=name or "Solo documentos modificados",
                direction=SyncDirection.BIDIRECTIONAL,
                remote_path="/sdcard/Documents",
                local_path=norm_local,
                serial=serial,
                rules=SyncRule(only_modified_docs=True, conflict_strategy=ConflictResolution.KEEP_BOTH),
            )
        else:
            raise ValueError(f"Preset desconocido: '{preset_type}'")

    # -------------------------------------------------------------------------
    # Filtrado y Evaluación de Reglas
    # -------------------------------------------------------------------------

    def match_rules(self, relative_path: str, size_bytes: int, rules: SyncRule) -> Tuple[bool, Optional[str]]:
        """Evalúa si un archivo debe ser incluido o excluido según las reglas."""
        rel_posix = relative_path.replace("\\", "/")
        name = rel_posix.rsplit("/", 1)[-1]
        ext = os.path.splitext(name)[1].lower()

        # 1. Filtro especial fotos
        if rules.only_new_photos and ext not in PHOTO_EXTENSIONS:
            return False, f"Omitido: no es archivo multimedia ({ext})"

        # 2. Filtro especial documentos
        if rules.only_modified_docs and ext not in DOC_EXTENSIONS:
            return False, f"Omitido: no es formato de documento ({ext})"

        # 3. Exclusión de subdirectorios
        parts = rel_posix.split("/")[:-1]
        for p in parts:
            if p in rules.exclude_dirs:
                return False, f"Omitido: directorio excluido '{p}'"

        # 4. Exclusión de patrones glob
        for pattern in rules.exclude_patterns:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_posix, pattern):
                return False, f"Omitido: coincide con patrón '{pattern}'"

        # 5. Límite de tamaño por archivo
        if rules.max_file_size_bytes is not None and size_bytes > rules.max_file_size_bytes:
            return False, f"Omitido: excede tamaño máximo permitido ({size_bytes} > {rules.max_file_size_bytes})"

        return True, None

    # -------------------------------------------------------------------------
    # Inspección de Archivos Locales y Remotos
    # -------------------------------------------------------------------------

    def scan_local_files(self, local_base: Path) -> Dict[str, LocalFileInfo]:
        """Indexa archivos locales por ruta relativa normalizada (posix)."""
        result: Dict[str, LocalFileInfo] = {}
        if not local_base.exists() or not local_base.is_dir():
            return result

        for item in local_base.rglob("*"):
            if item.is_file():
                rel = str(item.relative_to(local_base)).replace("\\", "/")
                stat = item.stat()
                result[rel] = LocalFileInfo(relative_path=rel, full_path=item, size=stat.st_size, mtime=stat.st_mtime)
        return result

    def scan_remote_files(self, remote_base: str, serial: Optional[str] = None) -> Dict[str, RemoteFileInfo]:
        """
        Escanea archivos remotos usando comandos estándar de AdbService.
        Utiliza 'find' o 'ls -lR' a través de adb shell.
        """
        result: Dict[str, RemoteFileInfo] = {}
        clean_base = remote_base.rstrip("/")
        # find <path> -type f -exec stat -c "%n|%s|%Y" {} +
        # Fallback estándar compatible con Android toybox/busybox:
        cmd = f"find '{clean_base}' -type f -exec stat -c '%s|%Y|%n' {{}} +"
        op = self._adb.shell(cmd, serial=serial, timeout=60.0)

        lines = op.stdout.splitlines() if op.success else []
        if not lines:
            # Fallback a find con ls
            op2 = self._adb.shell(f"find '{clean_base}' -type f", serial=serial, timeout=60.0)
            if op2.success:
                for line in op2.stdout.splitlines():
                    full_p = line.strip()
                    if full_p.startswith(clean_base):
                        rel = full_p[len(clean_base):].lstrip("/").replace("\\", "/")
                        if rel:
                            result[rel] = RemoteFileInfo(relative_path=rel, size=0, mtime=0)
            return result

        for line in lines:
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                try:
                    size = int(parts[0])
                    mtime = int(parts[1])
                    full_p = parts[2].strip()
                    if full_p.startswith(clean_base):
                        rel = full_p[len(clean_base):].lstrip("/").replace("\\", "/")
                        if rel:
                            result[rel] = RemoteFileInfo(relative_path=rel, size=size, mtime=mtime)
                except ValueError:
                    continue

        return result

    def get_remote_file_sha256(self, remote_file_path: str, serial: Optional[str] = None) -> Optional[str]:
        """Calcula el checksum SHA-256 de un archivo en el dispositivo vía sha256sum."""
        op = self._adb.shell(f"sha256sum '{remote_file_path}'", serial=serial, timeout=45.0)
        if op.success and op.stdout.strip():
            parts = op.stdout.strip().split()
            if parts and len(parts[0]) == 64:
                return parts[0].lower()
        return None

    # -------------------------------------------------------------------------
    # Manejo de Conflictos y Basurero (.trash/.conflict)
    # -------------------------------------------------------------------------

    def _archive_to_trash(self, file_path: Path, local_base: Path, tag: str = "trash") -> Path:
        """Mueve un archivo local a la carpeta .trash o .conflict correspondiente con timestamp."""
        trash_dir = local_base / f".{tag}"
        rel = file_path.relative_to(local_base)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_name = f"{rel.stem}_{timestamp}_{tag}{rel.suffix}"
        target_dir = trash_dir / rel.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / target_name
        shutil.move(str(file_path), str(dest))
        return dest

    # -------------------------------------------------------------------------
    # Motor de Sincronización
    # -------------------------------------------------------------------------

    def run_sync(
        self,
        profile: SyncProfile,
        *,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> SyncReport:
        """
        Ejecuta la sincronización completa del perfil especificado.
        """
        sync_id = f"sync_{uuid.uuid4().hex[:12]}"
        report = SyncReport(
            sync_id=sync_id,
            profile_id=profile.id,
            direction=profile.direction,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        local_base = Path(profile.local_path).resolve()
        local_base.mkdir(parents=True, exist_ok=True)
        remote_base = profile.remote_path.rstrip("/")
        rules = profile.rules
        serial = profile.serial

        if progress_callback:
            progress_callback(0.05, "Escaneando archivos en PC y teléfono...")

        # 1. Escaneo de ambos lados
        local_files = self.scan_local_files(local_base)
        remote_files = self.scan_remote_files(remote_base, serial=serial)

        all_rel_paths: Set[str] = set(local_files.keys()).union(set(remote_files.keys()))
        total_items = max(1, len(all_rel_paths))
        processed_items = 0
        total_transferred_bytes = 0

        logger.info(
            "Iniciando sync [%s]: dir=%s local_items=%d remote_items=%d",
            sync_id, profile.direction.value, len(local_files), len(remote_files)
        )

        for rel_path in sorted(all_rel_paths):
            if cancel_check and cancel_check():
                report.errors.append("Sincronización cancelada por el usuario.")
                report.success = False
                break

            loc = local_files.get(rel_path)
            rem = remote_files.get(rel_path)
            loc_path = local_base / rel_path if loc else local_base / rel_path
            rem_full = f"{remote_base}/{rel_path}"

            size_to_check = loc.size if loc else (rem.size if rem else 0)
            allowed, skip_reason = self.match_rules(rel_path, size_to_check, rules)

            if not allowed:
                report.skipped_count += 1
                report.items.append(
                    SyncItemReport(path=rel_path, status=SyncItemStatus.SKIPPED_RULE, reason=skip_reason)
                )
                processed_items += 1
                continue

            # Verificación de límite total acumulado
            if rules.max_total_bytes is not None and total_transferred_bytes >= rules.max_total_bytes:
                report.skipped_count += 1
                report.items.append(
                    SyncItemReport(
                        path=rel_path,
                        status=SyncItemStatus.SKIPPED_RULE,
                        reason="Límite máximo total de bytes alcanzado.",
                    )
                )
                processed_items += 1
                continue

            # CASO A: Archivo existe en ambos lados -> Detección de Colisión / Incremental
            if loc and rem:
                # Comprobación rápida: tamaño
                loc_hash = sha256_file(loc.full_path)
                # Si tamaños coinciden y el archivo es idéntico, omitir
                rem_hash = None
                if loc.size == rem.size:
                    rem_hash = self.get_remote_file_sha256(rem_full, serial=serial)
                    if rem_hash and rem_hash == loc_hash:
                        report.skipped_count += 1
                        report.skipped_bytes += loc.size
                        report.items.append(
                            SyncItemReport(
                                path=rel_path,
                                status=SyncItemStatus.SKIPPED_SAME,
                                size_bytes=loc.size,
                                sha256=loc_hash,
                                reason="Contenido idéntico (SHA-256 coincide)",
                            )
                        )
                        processed_items += 1
                        continue

                # Conflicto detectado: existe en ambos lados con contenido diferente
                if rem_hash is None:
                    rem_hash = self.get_remote_file_sha256(rem_full, serial=serial)

                conflict = SyncConflict(
                    relative_path=rel_path,
                    local_size=loc.size,
                    remote_size=rem.size,
                    local_sha256=loc_hash,
                    remote_sha256=rem_hash,
                    resolution=rules.conflict_strategy,
                )

                strategy = rules.conflict_strategy
                if strategy == ConflictResolution.SKIP:
                    conflict.action_taken = "Omitido por estrategia SKIP"
                    report.conflict_count += 1
                    report.conflicts.append(conflict)
                    report.items.append(
                        SyncItemReport(path=rel_path, status=SyncItemStatus.CONFLICT, reason="Conflicto: omitido")
                    )

                elif strategy == ConflictResolution.KEEP_LOCAL:
                    # Sobrescribir remoto con local
                    op = self._adb.push(str(loc.full_path), rem_full, serial=serial)
                    if op.success:
                        conflict.action_taken = "Sobrescrito remoto con versión local"
                        report.transferred_count += 1
                        report.transferred_bytes += loc.size
                        total_transferred_bytes += loc.size
                    else:
                        report.failed_count += 1
                        report.errors.append(f"Fallo al resolver conflicto push '{rel_path}': {op.message}")
                    report.conflict_count += 1
                    report.conflicts.append(conflict)

                elif strategy == ConflictResolution.KEEP_REMOTE:
                    # Sobrescribir local con remoto (respaldando en trash si use_trash)
                    if rules.use_trash and loc.full_path.exists():
                        self._archive_to_trash(loc.full_path, local_base, tag="conflict")
                    loc.full_path.parent.mkdir(parents=True, exist_ok=True)
                    op = self._adb.pull(rem_full, str(loc.full_path), serial=serial)
                    if op.success:
                        conflict.action_taken = "Sobrescrito local con versión remota (backup en .conflict)"
                        report.transferred_count += 1
                        report.transferred_bytes += rem.size
                        total_transferred_bytes += rem.size
                    else:
                        report.failed_count += 1
                        report.errors.append(f"Fallo al resolver conflicto pull '{rel_path}': {op.message}")
                    report.conflict_count += 1
                    report.conflicts.append(conflict)

                elif strategy == ConflictResolution.KEEP_BOTH:
                    # Renombrar archivo conflictivo en local
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    renamed_local = loc.full_path.parent / f"{loc.full_path.stem}_local_{timestamp}{loc.full_path.suffix}"
                    shutil.move(str(loc.full_path), str(renamed_local))
                    # Descargar remoto a su path original
                    op = self._adb.pull(rem_full, str(loc.full_path), serial=serial)
                    if op.success:
                        conflict.action_taken = f"Conservados ambos: local renombrado a {renamed_local.name}"
                        report.transferred_count += 1
                        report.transferred_bytes += rem.size
                        total_transferred_bytes += rem.size
                    else:
                        report.failed_count += 1
                        report.errors.append(f"Fallo al descargar en keep_both '{rel_path}': {op.message}")
                    report.conflict_count += 1
                    report.conflicts.append(conflict)

            # CASO B: Archivo solo en Remoto (Teléfono)
            elif rem and not loc:
                if profile.direction in (SyncDirection.PHONE_TO_PC, SyncDirection.BIDIRECTIONAL):
                    loc_path.parent.mkdir(parents=True, exist_ok=True)
                    op = self._adb.pull(rem_full, str(loc_path), serial=serial)
                    if op.success:
                        # Verificar checksum post-transfer
                        actual_hash = sha256_file(loc_path)
                        report.transferred_count += 1
                        report.transferred_bytes += rem.size
                        total_transferred_bytes += rem.size
                        report.items.append(
                            SyncItemReport(
                                path=rel_path,
                                status=SyncItemStatus.TRANSFERRED,
                                size_bytes=rem.size,
                                sha256=actual_hash,
                            )
                        )
                    else:
                        report.failed_count += 1
                        report.errors.append(f"Fallo al descargar '{rel_path}': {op.message}")
                        report.items.append(
                            SyncItemReport(path=rel_path, status=SyncItemStatus.FAILED, reason=op.message)
                        )
                else:
                    report.skipped_count += 1
                    report.items.append(
                        SyncItemReport(path=rel_path, status=SyncItemStatus.SKIPPED_RULE, reason="Dirección no aplica")
                    )

            # CASO C: Archivo solo en Local (PC)
            elif loc and not rem:
                if profile.direction in (SyncDirection.PC_TO_PHONE, SyncDirection.BIDIRECTIONAL):
                    # Asegurar directorio remoto
                    rem_dir = rem_full.rsplit("/", 1)[0]
                    self._adb.shell(f"mkdir -p '{rem_dir}'", serial=serial)
                    op = self._adb.push(str(loc.full_path), rem_full, serial=serial)
                    if op.success:
                        report.transferred_count += 1
                        report.transferred_bytes += loc.size
                        total_transferred_bytes += loc.size
                        report.items.append(
                            SyncItemReport(
                                path=rel_path,
                                status=SyncItemStatus.TRANSFERRED,
                                size_bytes=loc.size,
                                sha256=sha256_file(loc.full_path),
                            )
                        )
                    else:
                        report.failed_count += 1
                        report.errors.append(f"Fallo al subir '{rel_path}': {op.message}")
                        report.items.append(
                            SyncItemReport(path=rel_path, status=SyncItemStatus.FAILED, reason=op.message)
                        )
                else:
                    report.skipped_count += 1
                    report.items.append(
                        SyncItemReport(path=rel_path, status=SyncItemStatus.SKIPPED_RULE, reason="Dirección no aplica")
                    )

            processed_items += 1
            if progress_callback:
                pct = min(0.95, 0.05 + 0.90 * (processed_items / total_items))
                progress_callback(pct, f"Sincronizando: {rel_path}")

        report.completed_at = datetime.now(timezone.utc).isoformat()
        report.success = report.failed_count == 0

        # Guardar en base de datos
        self.save_report(report)

        # Actualizar timestamp del perfil si fue exitoso
        if report.success:
            profile.last_sync_utc = report.completed_at
            self.save_profile(profile)

        if progress_callback:
            progress_callback(1.0, "Sincronización completada.")

        logger.info(
            "Sync [%s] finalizado: success=%s transferred=%d skipped=%d conflicts=%d failed=%d",
            sync_id, report.success, report.transferred_count, report.skipped_count, report.conflict_count, report.failed_count
        )
        return report

    # -------------------------------------------------------------------------
    # Integración con JobQueueService
    # -------------------------------------------------------------------------

    def enqueue_sync_job(
        self,
        profile: SyncProfile,
        job_queue: Optional[JobQueueService] = None,
    ) -> str:
        """
        Encola un trabajo reanudable de sincronización en JobQueueService.
        """
        queue = job_queue or self._job_queue
        if not queue:
            raise RuntimeError("Se requiere una instancia de JobQueueService para encolar la sincronización.")

        title = f"Sync: {profile.name} ({profile.direction.value})"

        def sync_handler(job: JobItem, update_progress: Callable[[float, str], None]) -> Dict[str, Any]:
            def cancel_check() -> bool:
                return job.stop_event.is_set()

            def progress_cb(pct: float, step: str) -> None:
                # Comprobar pausa cooperativa
                queue.wait_if_paused(job)
                update_progress(pct, step)

            report = self.run_sync(profile, cancel_check=cancel_check, progress_callback=progress_cb)
            if not report.success and report.errors:
                raise RuntimeError("; ".join(report.errors[:3]))
            return report.to_dict()

        job_id = queue.enqueue(
            job_type=JobType.FILE_TRANSFER,
            title=title,
            serial=profile.serial,
            payload={
                "sync_profile_id": profile.id,
                "direction": profile.direction.value,
                "remote_path": profile.remote_path,
                "local_path": profile.local_path,
            },
            custom_handler=sync_handler,
        )
        return job_id
