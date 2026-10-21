"""Planificación y ejecución local de transferencias reanudables.

La capa no invoca ADB directamente: produce un plan y expone primitivas que
pueden ser usadas por un handler de JobQueueService o por AdbService. Así se
mantiene separada de la implementación concreta de PowerShell/ADB.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .adb_models import TransferJob, TransferJobStatus


@dataclass(frozen=True)
class TransferItem:
    source: Path
    destination: Path
    size: int
    source_sha256: str
    skip: bool = False


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_files(source: Path) -> Iterable[Path]:
    if source.is_file():
        yield source
        return
    yield from (item for item in source.rglob("*") if item.is_file())


def build_transfer_plan(source: Path | str, destination: Path | str, *,
                        skip_existing: bool = True, only_changes: bool = False) -> List[TransferItem]:
    """Calcula archivos, tamaños y hashes antes de iniciar una transferencia."""
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    root = src.parent if src.is_file() else src
    plan: List[TransferItem] = []
    for item in iter_files(src):
        rel = item.name if src.is_file() else str(item.relative_to(root))
        target = dst / rel
        digest = sha256_file(item)
        same = target.is_file() and target.stat().st_size == item.stat().st_size and sha256_file(target) == digest
        skip = bool(same and (skip_existing or only_changes))
        plan.append(TransferItem(item, target, item.stat().st_size, digest, skip))
    return plan


def execute_local_plan(job: TransferJob, plan: Iterable[TransferItem], *,
                       cancel: Optional[Callable[[], bool]] = None,
                       on_progress: Optional[Callable[[TransferJob], None]] = None) -> TransferJob:
    """Ejecuta un plan local con checkpoint por archivo y checksum final."""
    items = list(plan)
    job.files_total = len(items)
    job.bytes_total = sum(item.size for item in items if not item.skip)
    done_bytes = 0
    done_files = 0
    for item in items:
        if cancel and cancel():
            job.status = TransferJobStatus.CANCELLED
            return job
        if item.skip:
            done_files += 1
            job.checksums[str(item.destination)] = item.source_sha256
            job.mark_progress(bytes_completed=done_bytes, files_completed=done_files, current_file=str(item.source))
            continue
        try:
            item.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.source, item.destination)
            actual = sha256_file(item.destination)
            if actual != item.source_sha256:
                raise IOError(f"Checksum mismatch: {item.destination}")
            done_bytes += item.size
            done_files += 1
            job.checksums[str(item.destination)] = actual
            job.mark_progress(bytes_completed=done_bytes, files_completed=done_files, current_file=str(item.source))
        except Exception as exc:
            job.failed_files.append(str(item.source))
            job.fail(str(exc))
            return job
        if on_progress:
            on_progress(job)
    job.complete()
    if on_progress:
        on_progress(job)
    return job

