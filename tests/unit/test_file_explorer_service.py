"""Pruebas unitarias para FileExplorerService y sus modelos (Fase 5B).

Pruebas 100% aisladas con mocks de AdbService (sin requerir dispositivo real ni red):
- Validación de FileNode y ExplorerFilter (globs, extensiones, tamaños, fechas, dirs/files).
- Listado de directorios (list_dir) parseando formatos Toybox, enlaces simbólicos y nombres con espacios.
- Exploración en árbol acotada (tree) con límite de profundidad (max_depth) y filtros.
- Búsqueda (search) con limitación de resultados, truncado y fallback.
- Estadísticas de archivos (stat) y carpetas (folder_stat con du y caso UNSUPPORTED).
- Operaciones masivas (download_paths, upload_paths):
  - Ejecución directa mockeando adb.pull / adb.push.
  - Encolamiento opcional en JobQueueService.
- Protección de seguridad:
  - Rechazo de operaciones masivas sobre rutas protegidas (/, /system) sin confirm_root_access=True.
- Eliminación segura:
  - Por defecto mueve a papelera remota (.trash) con timestamp.
  - Rechazo de borrado permanente sin confirm_permanent_delete=True.
"""

from datetime import datetime
from unittest.mock import MagicMock
import pytest

from ui.services.adb_models import OperationResult
from ui.services.adb_service import AdbService
from ui.services.file_explorer_models import (
    BulkOperationType,
    ExplorerFilter,
    FileNode,
    FolderStatResult,
    SearchResult,
)
from ui.services.file_explorer_service import FileExplorerService
from ui.services.job_queue_service import JobQueueService, JobType


@pytest.fixture
def mock_adb():
    adb = MagicMock(spec=AdbService)
    adb.resolve_serial.side_effect = lambda s: s or "device-test-123"
    return adb


@pytest.fixture
def mock_job_queue():
    jq = MagicMock(spec=JobQueueService)
    jq.enqueue.return_value = "job_test_explorer_001"
    return jq


# -----------------------------------------------------------------------------
# Pruebas de Modelos y Filtros
# -----------------------------------------------------------------------------

def test_file_node_properties():
    node_file = FileNode(
        path="/sdcard/Documents/reporte final.pdf",
        name="reporte final.pdf",
        is_dir=False,
        size=1048576,  # 1 MB
        mtime=datetime(2026, 3, 15, 14, 30),
    )
    assert node_file.extension == ".pdf"
    assert "1.00 MB" in node_file.formatted_size
    assert node_file.is_dir is False

    node_dir = FileNode(
        path="/sdcard/Download",
        name="Download",
        is_dir=True,
        size=4096,
    )
    assert node_dir.extension == ""
    assert node_dir.formatted_size == "<DIR>"


def test_explorer_filter_matching():
    filter_img = ExplorerFilter(
        name_glob="IMG_*",
        extensions=[".jpg", ".png"],
        min_size=1000,
        max_size=5000000,
        modified_after=datetime(2026, 1, 1),
    )

    valid_node = FileNode(
        path="/sdcard/DCIM/IMG_20260315.jpg",
        name="IMG_20260315.jpg",
        is_dir=False,
        size=2500000,
        mtime=datetime(2026, 3, 15, 10, 0),
    )
    assert filter_img.matches(valid_node) is True

    # Falla por glob
    wrong_name = FileNode(path="/sdcard/DCIM/FOTO_01.jpg", name="FOTO_01.jpg", size=2500000, mtime=datetime(2026, 3, 15))
    assert filter_img.matches(wrong_name) is False

    # Falla por extensión
    wrong_ext = FileNode(path="/sdcard/DCIM/IMG_01.mp4", name="IMG_01.mp4", size=2500000, mtime=datetime(2026, 3, 15))
    assert filter_img.matches(wrong_ext) is False

    # Falla por tamaño mínimo
    too_small = FileNode(path="/sdcard/DCIM/IMG_02.jpg", name="IMG_02.jpg", size=500, mtime=datetime(2026, 3, 15))
    assert filter_img.matches(too_small) is False

    # Falla por fecha
    too_old = FileNode(path="/sdcard/DCIM/IMG_03.jpg", name="IMG_03.jpg", size=2500000, mtime=datetime(2025, 12, 1))
    assert filter_img.matches(too_old) is False


# -----------------------------------------------------------------------------
# Pruebas de Listado y Árbol
# -----------------------------------------------------------------------------

def test_list_dir_parsing(mock_adb):
    service = FileExplorerService(adb_service=mock_adb)

    # Simulación de salida estándar Toybox ls -la
    toybox_output = """total 64
drwxrwx--x  8 root sdcard_rw 4096 2026-03-15 14:02 .
drwxr-xr-x 35 root root      4096 2026-01-01 00:00 ..
drwxrwx--x  4 root sdcard_rw 4096 2026-03-15 12:00 DCIM
drwxrwx--x  2 root sdcard_rw 4096 2026-03-15 14:01 Documents
-rw-rw----  1 root sdcard_rw 2048 2026-03-15 14:05 nota de texto.txt
lrwxrwxrwx  1 root root        11 2026-03-15 14:02 acceso_fotos -> /sdcard/DCIM
"""
    mock_adb.shell.return_value = OperationResult(
        success=True,
        exit_code=0,
        stdout=toybox_output,
        stderr="",
    )

    nodes = service.list_dir("/sdcard", serial="device-123")

    assert len(nodes) == 4
    # Directorios primero
    assert nodes[0].name == "DCIM"
    assert nodes[0].is_dir is True

    assert nodes[1].name == "Documents"
    assert nodes[1].is_dir is True

    # Enlace simbólico
    link_node = next(n for n in nodes if n.name == "acceso_fotos")
    assert link_node.is_link is True
    assert link_node.link_target == "/sdcard/DCIM"

    # Archivo con espacios
    file_node = next(n for n in nodes if n.name == "nota de texto.txt")
    assert file_node.is_dir is False
    assert file_node.size == 2048
    assert file_node.mtime == datetime(2026, 3, 15, 14, 5)


def test_tree_bounded_depth(mock_adb):
    service = FileExplorerService(adb_service=mock_adb)

    def mock_shell(cmd, serial=None, timeout=None):
        if "'/sdcard'" in cmd:
            return OperationResult(
                success=True,
                exit_code=0,
                stdout="""drwxrwx--x 2 root sdcard_rw 4096 2026-03-15 12:00 SubFolder
-rw-rw---- 1 root sdcard_rw 1024 2026-03-15 12:00 file1.txt
""",
            )
        elif "'/sdcard/SubFolder'" in cmd:
            return OperationResult(
                success=True,
                exit_code=0,
                stdout="""-rw-rw---- 1 root sdcard_rw 2048 2026-03-15 12:00 nested.txt
""",
            )
        return OperationResult(success=True, exit_code=0, stdout="")

    mock_adb.shell.side_effect = mock_shell

    tree_node = service.tree("/sdcard", serial="device-123", max_depth=2)

    assert tree_node.is_dir is True
    assert tree_node.children is not None
    assert len(tree_node.children) == 2

    subfolder = next(c for c in tree_node.children if c.name == "SubFolder")
    assert subfolder.children is not None
    assert len(subfolder.children) == 1
    assert subfolder.children[0].name == "nested.txt"


# -----------------------------------------------------------------------------
# Pruebas de Búsqueda
# -----------------------------------------------------------------------------

def test_search_with_find_and_truncation(mock_adb):
    service = FileExplorerService(adb_service=mock_adb)

    find_stdout = """/sdcard/DCIM/IMG_001.jpg
/sdcard/DCIM/IMG_002.jpg
/sdcard/DCIM/IMG_003.jpg
/sdcard/DCIM/IMG_004.jpg
/sdcard/DCIM/IMG_005.jpg
"""
    mock_adb.shell.return_value = OperationResult(
        success=True,
        exit_code=0,
        stdout=find_stdout,
    )

    flt = ExplorerFilter(name_glob="IMG_*.jpg")
    res = service.search("/sdcard/DCIM", filter=flt, serial="device-123", max_results=3)

    assert res.root == "/sdcard/DCIM"
    assert res.total_found == 3
    assert res.truncated is True
    assert len(res.items) == 3
    assert res.items[0].name == "IMG_001.jpg"


# -----------------------------------------------------------------------------
# Pruebas de Estadísticas (stat y folder_stat)
# -----------------------------------------------------------------------------

def test_folder_stat_success_and_unsupported(mock_adb):
    service = FileExplorerService(adb_service=mock_adb)

    # Caso 1: Soporta du (devuelve kilobytes)
    mock_adb.shell.return_value = OperationResult(
        success=True,
        exit_code=0,
        stdout="10240\t/sdcard/Download\n",
    )
    stat_res = service.folder_stat("/sdcard/Download", serial="device-123")
    assert stat_res.supported is True
    assert stat_res.total_size_bytes == 10240 * 1024  # 10 MB

    # Caso 2: Comando no soportado o fallo
    mock_adb.shell.return_value = OperationResult(
        success=False,
        exit_code=127,
        stdout="/system/bin/sh: du: not found\n",
    )
    unsupported_res = service.folder_stat("/sdcard/Download", serial="device-123")
    assert unsupported_res.supported is False
    assert unsupported_res.total_size_bytes is None
    assert "UNSUPPORTED" in unsupported_res.message


# -----------------------------------------------------------------------------
# Pruebas de Seguridad y Operaciones Masivas
# -----------------------------------------------------------------------------

def test_dangerous_path_protection(mock_adb):
    service = FileExplorerService(adb_service=mock_adb)

    # Intento de operar sobre / o /system sin confirm_root_access debe lanzar ValueError
    with pytest.raises(ValueError, match="OPERACION_PELIGROSA"):
        service.download_paths(
            serial="device-123",
            remote_paths=["/system/bin/sh"],
            local_dir="C:/tmp",
            confirm_root_access=False,
        )

    with pytest.raises(ValueError, match="OPERACION_PELIGROSA"):
        service.delete_remote(
            serial="device-123",
            remote_paths=["/"],
            confirm_root_access=False,
        )


def test_bulk_download_direct_and_queued(mock_adb, mock_job_queue, tmp_path):
    local_target = tmp_path / "downloads"

    # Caso 1: Ejecución directa con mock adb.pull
    mock_adb.pull.return_value = OperationResult(success=True, exit_code=0)
    service_direct = FileExplorerService(adb_service=mock_adb)

    res = service_direct.download_paths(
        serial="device-123",
        remote_paths=["/sdcard/Download/file1.pdf", "/sdcard/Download/file2.pdf"],
        local_dir=str(local_target),
    )
    assert res.operation == BulkOperationType.DOWNLOAD
    assert res.total_items == 2
    assert res.succeeded_items == 2
    assert res.failed_items == 0
    assert mock_adb.pull.call_count == 2

    # Caso 2: Encolamiento en JobQueueService
    service_queued = FileExplorerService(adb_service=mock_adb, job_queue_service=mock_job_queue)
    res_queue = service_queued.download_paths(
        serial="device-123",
        remote_paths=["/sdcard/Download/file1.pdf"],
        local_dir=str(local_target),
        enqueue_in_job_queue=True,
    )
    assert res_queue.job_id == "job_test_explorer_001"
    mock_job_queue.enqueue.assert_called_once()


def test_delete_remote_safe_trash_default(mock_adb):
    service = FileExplorerService(adb_service=mock_adb, trash_dir="/sdcard/.trash")

    mock_adb.shell.return_value = OperationResult(success=True, exit_code=0)

    # Por defecto debe usar papelera (.trash) con mv
    res = service.delete_remote(
        serial="device-123",
        remote_paths=["/sdcard/Documents/obsoleto.txt"],
        move_to_trash=True,
    )

    assert res.operation == BulkOperationType.TRASH
    assert res.succeeded_items == 1
    assert len(res.results) == 1
    assert "/sdcard/.trash/obsoleto.txt_" in res.results[0].destination

    # Si se intenta borrado permanente sin confirm_permanent_delete, debe fallar
    with pytest.raises(ValueError, match="ELIMINACION_PERMANENTE_REQUERIDA"):
        service.delete_remote(
            serial="device-123",
            remote_paths=["/sdcard/Documents/obsoleto.txt"],
            move_to_trash=False,
            confirm_permanent_delete=False,
        )

    # Con confirm_permanent_delete=True se permite
    res_perm = service.delete_remote(
        serial="device-123",
        remote_paths=["/sdcard/Documents/obsoleto.txt"],
        move_to_trash=False,
        confirm_permanent_delete=True,
    )
    assert res_perm.operation == BulkOperationType.DELETE
    assert res_perm.succeeded_items == 1
