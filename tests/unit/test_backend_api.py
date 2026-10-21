"""Pruebas unitarias completas para el Backend FastAPI y WebSockets (Fase 6B).

Pruebas 100% aisladas con TestClient y mocks de servicios (sin requerir dispositivo real):
- GET /: Verificación de estado de la API.
- /api/devices: Listado de dispositivos y validación de estados.
- /api/devices/{serial}/health: Snapshots de salud, errores 404/409/400.
- /api/backups: Consulta de perfiles y encolamiento de respaldos.
- /api/sync: Consulta de perfiles y encolamiento de sincronizaciones.
- /api/jobs: Consulta de historial, pausas, reanudaciones y cancelaciones.
- /api/explorer/list: Navegación de directorios remotos y validaciones de serial.
- /ws/jobs y /ws/jobs/{job_id}: WebSockets de progreso y eventos en tiempo real.
"""

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from backend.deps import (
    get_adb_service,
    get_backup_service,
    get_device_health_service,
    get_file_explorer_service,
    get_job_queue_service,
    get_sync_service,
)
from backend.main import create_app
from backend.websocket import manager
from ui.services.adb_models import Device, DeviceState
from ui.services.adb_service import AdbService
from ui.services.backup_service import BackupService
from ui.services.device_health_models import DeviceHealthSnapshot
from ui.services.device_health_service import DeviceHealthService
from ui.services.file_explorer_models import FileNode
from ui.services.file_explorer_service import FileExplorerService
from ui.services.job_queue_service import JobItem, JobQueueService, JobStatus, JobType
from ui.services.sync_profiles import ConflictResolution, SyncDirection, SyncProfile, SyncRule
from ui.services.sync_service import SyncService


@pytest.fixture
def mock_adb():
    adb = MagicMock(spec=AdbService)
    ready_dev = Device(
        serial="emulator-5554",
        state=DeviceState.DEVICE,
        model="Pixel_8",
        battery_level=85,
        android_version="14",
    )
    offline_dev = Device(
        serial="offline-device",
        state=DeviceState.OFFLINE,
        model="Galaxy_S21",
    )
    adb.devices.return_value = [ready_dev, offline_dev]
    return adb


@pytest.fixture
def mock_job_queue():
    jq = MagicMock(spec=JobQueueService)
    test_job = JobItem(
        id="job_test_api_001",
        job_type=JobType.FILE_TRANSFER,
        title="Sync: Fotos",
        serial="emulator-5554",
        status=JobStatus.RUNNING,
        progress_percent=0.5,
        current_step="Copiando fotos",
    )
    jq.get_history.return_value = [test_job]
    jq.get_active_jobs.return_value = [test_job]
    jq.get_job.side_effect = lambda jid: test_job if jid == "job_test_api_001" else None
    jq.pause.return_value = True
    jq.resume.return_value = True
    jq.cancel.return_value = True
    jq.enqueue.return_value = "job_test_api_002"
    return jq


@pytest.fixture
def mock_health():
    health = MagicMock(spec=DeviceHealthService)
    snapshot = DeviceHealthSnapshot(
        serial="emulator-5554",
        battery_level=85,
        battery_status="charging",
        storage_total_bytes=64000000000,
        storage_free_bytes=32000000000,
        ram_total_bytes=8000000000,
        ram_available_bytes=4000000000,
        model="Pixel_8",
        manufacturer="Google",
        android_version="14",
    )
    health.collect_snapshot.return_value = snapshot
    return health


@pytest.fixture
def mock_backup():
    bk = MagicMock(spec=BackupService)
    bk.enqueue_profile.return_value = "job_backup_001"
    return bk


@pytest.fixture
def mock_sync():
    sync = MagicMock(spec=SyncService)
    profile = SyncProfile(
        id="prof_photos",
        name="Sync Fotos",
        serial="emulator-5554",
        direction=SyncDirection.PHONE_TO_PC,
        remote_path="/sdcard/DCIM",
        local_path="C:/Backup/Photos",
        rules=SyncRule(),
        conflict_resolution=ConflictResolution.KEEP_LOCAL,
    )
    sync.list_profiles.return_value = [profile]
    sync.get_profile.side_effect = lambda pid: profile if pid == "prof_photos" else None
    sync.enqueue_sync_job.return_value = "job_sync_001"
    return sync


@pytest.fixture
def mock_explorer():
    exp = MagicMock(spec=FileExplorerService)
    exp.list_dir.return_value = [
        FileNode(path="/sdcard/Download", name="Download", is_dir=True),
        FileNode(path="/sdcard/test.txt", name="test.txt", is_dir=False, size=1024),
    ]
    return exp


@pytest.fixture
def client(mock_adb, mock_job_queue, mock_health, mock_backup, mock_sync, mock_explorer):
    app = create_app()
    app.dependency_overrides[get_adb_service] = lambda: mock_adb
    app.dependency_overrides[get_job_queue_service] = lambda: mock_job_queue
    app.dependency_overrides[get_device_health_service] = lambda: mock_health
    app.dependency_overrides[get_backup_service] = lambda: mock_backup
    app.dependency_overrides[get_sync_service] = lambda: mock_sync
    app.dependency_overrides[get_file_explorer_service] = lambda: mock_explorer

    with TestClient(app) as test_client:
        yield test_client


# -----------------------------------------------------------------------------
# Pruebas de Endpoints REST
# -----------------------------------------------------------------------------

def test_api_root(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "/api/devices" in data["endpoints"]


def test_list_devices(client):
    response = client.get("/api/devices")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["serial"] == "emulator-5554"
    assert data[0]["is_ready"] is True
    assert data[1]["serial"] == "offline-device"
    assert data[1]["is_ready"] is False


def test_device_health_success_and_errors(client):
    # Caso 1: Éxito
    res_ok = client.get("/api/devices/emulator-5554/health")
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["battery_level"] == 85
    assert data["model"] == "Pixel_8"

    # Caso 2: Dispositivo offline (409 Conflict)
    res_conflict = client.get("/api/devices/offline-device/health")
    assert res_conflict.status_code == 409
    assert "no está listo" in res_conflict.json()["detail"]

    # Caso 3: Dispositivo inexistente (404 Not Found)
    res_not_found = client.get("/api/devices/non-existent-device/health")
    assert res_not_found.status_code == 404


def test_backup_profiles_and_enqueue(client):
    # Listar perfiles
    res_prof = client.get("/api/backups/profiles")
    assert res_prof.status_code == 200
    profiles = res_prof.json()
    assert len(profiles) > 0
    assert any(p["id"] == "photos_videos" for p in profiles)

    # Encolar exitoso
    res_enq = client.post(
        "/api/backups/enqueue",
        json={"serial": "emulator-5554", "profile_id": "photos_videos"},
    )
    assert res_enq.status_code == 202
    assert res_enq.json()["job_id"] == "job_backup_001"

    # Encolar perfil inválido (404)
    res_invalid = client.post(
        "/api/backups/enqueue",
        json={"serial": "emulator-5554", "profile_id": "invalid_profile"},
    )
    assert res_invalid.status_code == 404

    # Encolar en dispositivo offline (409)
    res_offline = client.post(
        "/api/backups/enqueue",
        json={"serial": "offline-device", "profile_id": "photos_videos"},
    )
    assert res_offline.status_code == 409


def test_sync_profiles_and_enqueue(client):
    # Listar perfiles de sync
    res_list = client.get("/api/sync/profiles")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # Encolar sincronización
    res_enq = client.post(
        "/api/sync/enqueue",
        json={"profile_id": "prof_photos", "serial": "emulator-5554"},
    )
    assert res_enq.status_code == 202
    assert res_enq.json()["job_id"] == "job_sync_001"

    # Perfil no encontrado (404)
    res_not_found = client.post(
        "/api/sync/enqueue",
        json={"profile_id": "nonexistent_sync", "serial": "emulator-5554"},
    )
    assert res_not_found.status_code == 404


def test_jobs_management(client):
    # Listar trabajos
    res_jobs = client.get("/api/jobs")
    assert res_jobs.status_code == 200
    assert len(res_jobs.json()) == 1
    assert res_jobs.json()[0]["id"] == "job_test_api_001"

    # Obtener trabajo específico
    res_single = client.get("/api/jobs/job_test_api_001")
    assert res_single.status_code == 200
    assert res_single.json()["progress_percent"] == 0.5

    # Trabajo no encontrado (404)
    res_404 = client.get("/api/jobs/invalid_job_id")
    assert res_404.status_code == 404

    # Pausar trabajo
    res_pause = client.post("/api/jobs/job_test_api_001/pause")
    assert res_pause.status_code == 200

    # Reanudar trabajo
    res_resume = client.post("/api/jobs/job_test_api_001/resume")
    assert res_resume.status_code == 200

    # Cancelar trabajo
    res_cancel = client.post("/api/jobs/job_test_api_001/cancel")
    assert res_cancel.status_code == 200


def test_explorer_list(client):
    # Éxito
    res_exp = client.get("/api/explorer/list?serial=emulator-5554&path=/sdcard")
    assert res_exp.status_code == 200
    nodes = res_exp.json()
    assert len(nodes) == 2
    assert nodes[0]["name"] == "Download"

    # Error si falta serial o está vacío
    res_no_serial = client.get("/api/explorer/list?serial=&path=/sdcard")
    assert res_no_serial.status_code == 422 or res_no_serial.status_code == 400

    # Error si dispositivo no existe
    res_no_dev = client.get("/api/explorer/list?serial=desconocido&path=/sdcard")
    assert res_no_dev.status_code == 404


# -----------------------------------------------------------------------------
# Pruebas de WebSockets
# -----------------------------------------------------------------------------

def test_websocket_jobs_global(client):
    with client.websocket_connect("/ws/jobs") as websocket:
        init_data = websocket.receive_json()
        assert init_data["event"] == "connected"

        # Simular notificación de trabajo disparada por el gestor
        manager._on_job_event(
            JobItem(
                id="job_ws_001",
                job_type=JobType.FILE_TRANSFER,
                title="Prueba WS",
                serial="emulator-5554",
                status=JobStatus.RUNNING,
                progress_percent=0.75,
                current_step="Enviando archivo...",
            )
        )
        update_data = websocket.receive_json()
        assert update_data["event"] == "job_update"
        assert update_data["job_id"] == "job_ws_001"
        assert update_data["progress_percent"] == 0.75


def test_websocket_job_single(client):
    # Conectar a job existente
    with client.websocket_connect("/ws/jobs/job_test_api_001") as websocket:
        init_data = websocket.receive_json()
        assert init_data["event"] == "job_init"
        assert init_data["job_id"] == "job_test_api_001"

    # Conectar a job inexistente (debe recibir error y cerrar)
    with client.websocket_connect("/ws/jobs/nonexistent_job") as websocket:
        err_data = websocket.receive_json()
        assert err_data["event"] == "error"
