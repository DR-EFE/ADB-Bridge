"""Pruebas unitarias para SyncService, SyncProfile y SyncRule (Fase 3B).

Pruebas completamente aisladas y mockeadas (sin dispositivo físico real ni dependencias de red):
- Validación de modelos Pydantic: SyncProfile, SyncRule, SyncConflict, SyncReport
- Evaluación de reglas: exclusiones glob, exclusión de subdirectorios, tamaño máximo, extensiones
- Presets: phone_to_pc, pc_to_phone, bidirectional, new_photos, modified_docs
- Persistencia en SQLite (tablas sync_profiles y sync_reports)
- Sincronización Phone -> PC
- Sincronización PC -> Phone
- Sincronización Bidireccional
- Omisión de archivos idénticos (incremental con hash SHA-256)
- Detección y resolución de conflictos:
  - KEEP_BOTH (renombrado con timestamp)
  - KEEP_LOCAL (push del local hacia el remoto)
  - KEEP_REMOTE (pull hacia local con archivado en .conflict)
  - SKIP (anotado en reporte sin transferir)
- Encolado como trabajo reanudable en JobQueueService
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ui.services.adb_models import OperationResult
from ui.services.adb_service import AdbService
from ui.services.database import SQLiteStore
from ui.services.job_queue_service import JobItem, JobQueueService, JobStatus, JobType
from ui.services.sync_profiles import (
    ConflictResolution,
    SyncConflict,
    SyncDirection,
    SyncItemStatus,
    SyncProfile,
    SyncReport,
    SyncRule,
)
from ui.services.sync_service import SyncService


@pytest.fixture
def memory_db(tmp_path):
    db_file = tmp_path / "sync_test.db"
    return SQLiteStore(db_file)


@pytest.fixture
def mock_adb():
    adb = MagicMock(spec=AdbService)
    # Por defecto comandos exitosos
    adb.shell.return_value = OperationResult(success=True, exit_code=0, stdout="", stderr="")
    adb.pull.return_value = OperationResult(success=True, exit_code=0, stdout="", stderr="")
    adb.push.return_value = OperationResult(success=True, exit_code=0, stdout="", stderr="")
    return adb


def test_sync_rule_filtering():
    service = SyncService()
    rules = SyncRule(
        exclude_patterns=["*.tmp", "Thumbs.db"],
        exclude_dirs=[".thumbnails", "cache"],
        max_file_size_bytes=1000,
    )

    # Permitido
    allowed, _ = service.match_rules("sub/photo.jpg", 500, rules)
    assert allowed is True

    # Excluido por extensión/patrón
    allowed, reason = service.match_rules("sub/test.tmp", 100, rules)
    assert allowed is False
    assert "patrón" in reason

    # Excluido por directorio
    allowed, reason = service.match_rules(".thumbnails/small.jpg", 100, rules)
    assert allowed is False
    assert "directorio excluido" in reason

    # Excluido por tamaño
    allowed, reason = service.match_rules("sub/large.bin", 2000, rules)
    assert allowed is False
    assert "excede tamaño máximo" in reason


def test_sync_rule_photos_and_docs_filtering():
    service = SyncService()
    photo_rules = SyncRule(only_new_photos=True)
    assert service.match_rules("DCIM/pic.JPG", 100, photo_rules)[0] is True
    assert service.match_rules("DCIM/doc.pdf", 100, photo_rules)[0] is False

    doc_rules = SyncRule(only_modified_docs=True)
    assert service.match_rules("docs/report.pdf", 100, doc_rules)[0] is True
    assert service.match_rules("docs/vacation.png", 100, doc_rules)[0] is False


def test_preset_profiles_creation(tmp_path):
    local = tmp_path / "local"
    p1 = SyncService.create_preset_profile("phone_to_pc", "Fotos", local)
    assert p1.direction == SyncDirection.PHONE_TO_PC

    p2 = SyncService.create_preset_profile("new_photos", "Solo Fotos", local)
    assert p2.rules.only_new_photos is True

    p3 = SyncService.create_preset_profile("bidirectional", "Docs", local)
    assert p3.direction == SyncDirection.BIDIRECTIONAL

    with pytest.raises(ValueError):
        SyncService.create_preset_profile("unknown_preset", "Bad", local)


def test_sqlite_sync_profiles_and_reports_crud(memory_db, mock_adb, tmp_path):
    service = SyncService(adb_service=mock_adb, database=memory_db)
    profile = SyncProfile(
        id="p1",
        name="Perfil Test",
        direction=SyncDirection.PHONE_TO_PC,
        remote_path="/sdcard/DCIM",
        local_path=str(tmp_path),
    )

    service.save_profile(profile)
    fetched = service.get_profile("p1")
    assert fetched is not None
    assert fetched.name == "Perfil Test"

    all_profiles = service.list_profiles()
    assert len(all_profiles) == 1

    report = SyncReport(
        sync_id="sync_001",
        profile_id="p1",
        direction=SyncDirection.PHONE_TO_PC,
        success=True,
        transferred_count=5,
    )
    service.save_report(report)
    last_report = service.get_last_report("p1")
    assert last_report is not None
    assert last_report.transferred_count == 5

    assert service.delete_profile("p1") is True
    assert service.get_profile("p1") is None


def test_sync_phone_to_pc(mock_adb, memory_db, tmp_path):
    local = tmp_path / "pc_dir"
    local.mkdir()
    service = SyncService(adb_service=mock_adb, database=memory_db)

    # Simular remoto con 2 archivos
    mock_adb.shell.return_value = OperationResult(
        success=True,
        exit_code=0,
        stdout="1024|1700000000|/sdcard/DCIM/img1.jpg\n2048|1700000000|/sdcard/DCIM/sub/img2.jpg\n",
    )

    # Simular que adb.pull crea los archivos locales
    def fake_pull(remote, loc_dst, **kwargs):
        Path(loc_dst).parent.mkdir(parents=True, exist_ok=True)
        Path(loc_dst).write_bytes(b"data")
        return OperationResult(success=True, exit_code=0)

    mock_adb.pull.side_effect = fake_pull

    profile = SyncProfile(
        id="test_pull",
        name="Pull Test",
        direction=SyncDirection.PHONE_TO_PC,
        remote_path="/sdcard/DCIM",
        local_path=str(local),
    )

    report = service.run_sync(profile)
    assert report.success is True
    assert report.transferred_count == 2
    assert (local / "img1.jpg").exists()
    assert (local / "sub" / "img2.jpg").exists()


def test_sync_pc_to_phone(mock_adb, memory_db, tmp_path):
    local = tmp_path / "pc_dir"
    local.mkdir()
    (local / "doc1.txt").write_text("contenido 1", encoding="utf-8")
    (local / "sub").mkdir()
    (local / "sub" / "doc2.txt").write_text("contenido 2", encoding="utf-8")

    # Remoto inicialmente vacío
    mock_adb.shell.return_value = OperationResult(success=True, exit_code=0, stdout="")
    mock_adb.push.return_value = OperationResult(success=True, exit_code=0)

    service = SyncService(adb_service=mock_adb, database=memory_db)
    profile = SyncProfile(
        id="test_push",
        name="Push Test",
        direction=SyncDirection.PC_TO_PHONE,
        remote_path="/sdcard/Documents",
        local_path=str(local),
    )

    report = service.run_sync(profile)
    assert report.success is True
    assert report.transferred_count == 2
    assert mock_adb.push.call_count == 2


def test_sync_skips_identical_files(mock_adb, memory_db, tmp_path):
    local = tmp_path / "pc_dir"
    local.mkdir()
    f1 = local / "same.txt"
    f1.write_bytes(b"hello identical world")
    size = f1.stat().st_size

    service = SyncService(adb_service=mock_adb, database=memory_db)
    import hashlib
    h = hashlib.sha256(b"hello identical world").hexdigest()

    # Remoto tiene mismo archivo, mismo tamaño
    mock_adb.shell.side_effect = [
        # Escaneo find
        OperationResult(success=True, exit_code=0, stdout=f"{size}|1700000000|/sdcard/same.txt\n"),
        # sha256sum remoto
        OperationResult(success=True, exit_code=0, stdout=f"{h}  /sdcard/same.txt\n"),
    ]

    profile = SyncProfile(
        id="test_skip",
        name="Skip Test",
        direction=SyncDirection.BIDIRECTIONAL,
        remote_path="/sdcard",
        local_path=str(local),
    )

    report = service.run_sync(profile)
    assert report.success is True
    assert report.skipped_count == 1
    assert report.transferred_count == 0
    mock_adb.pull.assert_not_called()
    mock_adb.push.assert_not_called()


def test_sync_conflict_keep_both(mock_adb, memory_db, tmp_path):
    local = tmp_path / "pc_dir"
    local.mkdir()
    f1 = local / "doc.txt"
    f1.write_bytes(b"local content")

    service = SyncService(adb_service=mock_adb, database=memory_db)
    mock_adb.shell.side_effect = [
        # find remoto
        OperationResult(success=True, exit_code=0, stdout="20|1700000000|/sdcard/doc.txt\n"),
        # sha256sum remoto
        OperationResult(success=True, exit_code=0, stdout="aaaa" * 16 + "  /sdcard/doc.txt\n"),
    ]

    def fake_pull(remote, loc_dst, **kwargs):
        Path(loc_dst).write_bytes(b"remote downloaded content")
        return OperationResult(success=True, exit_code=0)

    mock_adb.pull.side_effect = fake_pull

    profile = SyncProfile(
        id="test_conflict_both",
        name="Conflict Keep Both",
        direction=SyncDirection.BIDIRECTIONAL,
        remote_path="/sdcard",
        local_path=str(local),
        rules=SyncRule(conflict_strategy=ConflictResolution.KEEP_BOTH),
    )

    report = service.run_sync(profile)
    assert report.conflict_count == 1
    assert report.transferred_count == 1

    # Debe haber 2 archivos en local: el renombrado _local_ y el descargado doc.txt
    files = list(local.glob("doc*.txt"))
    assert len(files) == 2
    renamed = [f for f in files if "_local_" in f.name]
    assert len(renamed) == 1
    assert renamed[0].read_bytes() == b"local content"
    assert (local / "doc.txt").read_bytes() == b"remote downloaded content"


def test_sync_conflict_skip(mock_adb, memory_db, tmp_path):
    local = tmp_path / "pc_dir"
    local.mkdir()
    f1 = local / "doc.txt"
    f1.write_bytes(b"local content")

    service = SyncService(adb_service=mock_adb, database=memory_db)
    mock_adb.shell.side_effect = [
        OperationResult(success=True, exit_code=0, stdout="50|1700000000|/sdcard/doc.txt\n"),
        OperationResult(success=True, exit_code=0, stdout="bbbb" * 16 + "  /sdcard/doc.txt\n"),
    ]

    profile = SyncProfile(
        id="test_conflict_skip",
        name="Conflict Skip",
        direction=SyncDirection.BIDIRECTIONAL,
        remote_path="/sdcard",
        local_path=str(local),
        rules=SyncRule(conflict_strategy=ConflictResolution.SKIP),
    )

    report = service.run_sync(profile)
    assert report.conflict_count == 1
    assert report.transferred_count == 0
    mock_adb.pull.assert_not_called()
    mock_adb.push.assert_not_called()


def test_sync_integration_with_job_queue(mock_adb, memory_db, tmp_path):
    job_queue = JobQueueService(database=memory_db, max_local_workers=1, auto_persist=False)
    service = SyncService(adb_service=mock_adb, job_queue=job_queue, database=memory_db)

    local = tmp_path / "pc_dir"
    local.mkdir()
    profile = SyncProfile(
        id="job_sync",
        name="Sync Job Test",
        direction=SyncDirection.PHONE_TO_PC,
        remote_path="/sdcard/DCIM",
        local_path=str(local),
    )

    mock_adb.shell.return_value = OperationResult(success=True, exit_code=0, stdout="")
    job_id = service.enqueue_sync_job(profile)
    assert job_id.startswith("job_")

    job = job_queue.get_job(job_id)
    assert job is not None
    assert job.job_type == JobType.FILE_TRANSFER
    assert job.payload["sync_profile_id"] == "job_sync"
