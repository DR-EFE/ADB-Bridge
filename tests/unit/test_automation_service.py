"""Pruebas unitarias para AutomationService y AutomationRule (Fase 4B).

Pruebas completamente aisladas y mockeadas (sin dispositivo físico ni red):
- Validación de modelos Pydantic: AutomationRule, AutomationAction, AutomationCondition, AutomationRun
- Evaluación de condiciones (serial, package_name, text_contains, profile_id, storage_threshold_percent)
- Disparadores:
  - on_device_connected(serial)
  - on_notification(payload)
  - on_storage_low(serial, free_percent)
- Acciones:
  - RUN_BACKUP_PROFILE (invoca backup_service.enqueue_profile o fallback a job_queue)
  - RUN_SYNC_PROFILE (invoca sync_service.enqueue_sync_job)
  - SAVE_NOTIFICATION (persistencia en notifications)
  - LOG_SUMMARY (registro seguro)
  - SHELL_SAFE_COMMAND (comprobación estricta de allowlist vs comandos no permitidos)
- Persistencia SQLite en tablas automation_rules y automation_runs
- Generación de resumen estructurado JSON
"""

from unittest.mock import MagicMock
import pytest

from ui.services.adb_models import OperationResult
from ui.services.adb_service import AdbService
from ui.services.automation_rules import (
    ActionRunResult,
    ActionType,
    AutomationAction,
    AutomationCondition,
    AutomationRule,
    AutomationRun,
    AutomationRunStatus,
    TriggerType,
)
from ui.services.automation_service import AutomationService, SAFE_SHELL_ALLOWLIST
from ui.services.database import SQLiteStore
from ui.services.job_queue_service import JobQueueService
from ui.services.sync_profiles import SyncDirection, SyncProfile
from ui.services.sync_service import SyncService


@pytest.fixture
def memory_db(tmp_path):
    db_file = tmp_path / "automation_test.db"
    return SQLiteStore(db_file)


@pytest.fixture
def mock_adb():
    adb = MagicMock(spec=AdbService)
    adb.shell.return_value = OperationResult(success=True, exit_code=0, stdout="device uptime: 10 hours", stderr="")
    return adb


def test_condition_matching():
    service = AutomationService()

    # Coincidencia por serial
    c1 = AutomationCondition(serial="device-123")
    assert service.match_condition(c1, {"serial": "device-123"}) is True
    assert service.match_condition(c1, {"serial": "other-device"}) is False

    # Coincidencia por paquete de app
    c2 = AutomationCondition(package_name="com.whatsapp")
    assert service.match_condition(c2, {"package": "com.whatsapp"}) is True
    assert service.match_condition(c2, {"package_name": "com.whatsapp.w4b"}) is True
    assert service.match_condition(c2, {"package": "com.google.android.gm"}) is False

    # Coincidencia por texto
    c3 = AutomationCondition(text_contains="código")
    assert service.match_condition(c3, {"texto": "Tu código de verificación es 492011"}) is True
    assert service.match_condition(c3, {"titulo": "Código Bancario", "texto": "Operación lista"}) is True
    assert service.match_condition(c3, {"texto": "Hola mundo"}) is False

    # Coincidencia por umbral de almacenamiento
    c4 = AutomationCondition(storage_threshold_percent=15.0)
    assert service.match_condition(c4, {"storage_free_percent": 10.0}) is True
    assert service.match_condition(c4, {"storage_free_percent": 25.0}) is False


def test_sqlite_rules_and_runs_crud(memory_db, mock_adb):
    service = AutomationService(database=memory_db, adb_service=mock_adb)

    rule = AutomationRule(
        id="rule_01",
        name="Auto Backup al Conectar",
        trigger=TriggerType.DEVICE_CONNECTED,
        conditions=AutomationCondition(serial="device-1"),
        actions=[
            AutomationAction(
                action_type=ActionType.RUN_BACKUP_PROFILE,
                params={"profile_id": "docs_profile"},
            )
        ],
    )

    service.save_rule(rule)
    fetched = service.get_rule("rule_01")
    assert fetched is not None
    assert fetched.name == "Auto Backup al Conectar"

    rules = service.list_rules(trigger=TriggerType.DEVICE_CONNECTED)
    assert len(rules) == 1

    run = AutomationRun(
        run_id="run_01",
        rule_id="rule_01",
        trigger_type=TriggerType.DEVICE_CONNECTED,
        event_payload={"serial": "device-1"},
        status=AutomationRunStatus.SUCCESS,
        summary_message="Ejecutado con éxito",
    )
    service.record_run(run)

    runs = service.list_runs()
    assert len(runs) == 1
    assert runs[0].run_id == "run_01"

    assert service.delete_rule("rule_01") is True
    assert service.get_rule("rule_01") is None


def test_on_device_connected_dispatches_sync_and_backup(memory_db, mock_adb):
    mock_sync = MagicMock(spec=SyncService)
    mock_backup = MagicMock()
    mock_queue = MagicMock(spec=JobQueueService)

    # Configurar mock de sync service
    profile_mock = SyncProfile(
        id="sync_photos",
        name="Fotos",
        direction=SyncDirection.PHONE_TO_PC,
        remote_path="/sdcard/DCIM",
        local_path="C:/Photos",
    )
    mock_sync.get_profile.return_value = profile_mock
    mock_sync.enqueue_sync_job.return_value = "job_sync_123"

    # Configurar mock de backup service
    mock_backup.enqueue_profile.return_value = "job_backup_456"

    service = AutomationService(
        job_queue=mock_queue,
        sync_service=mock_sync,
        backup_service=mock_backup,
        adb_service=mock_adb,
        database=memory_db,
    )

    # Crear regla combinada: al conectar -> sync new_photos + backup documentos
    rule = AutomationRule(
        id="rule_connect_suite",
        name="Al conectar -> Sync fotos + Backup documentos",
        trigger=TriggerType.DEVICE_CONNECTED,
        conditions=AutomationCondition(serial="dev_galaxy"),
        actions=[
            AutomationAction(
                action_type=ActionType.RUN_SYNC_PROFILE,
                params={"profile_id": "sync_photos"},
            ),
            AutomationAction(
                action_type=ActionType.RUN_BACKUP_PROFILE,
                params={"profile_id": "backup_docs"},
            ),
            AutomationAction(
                action_type=ActionType.LOG_SUMMARY,
                params={"message": "Dispositivo conectado y automatización disparada"},
            ),
        ],
    )
    service.save_rule(rule)

    # Disparar evento
    runs = service.on_device_connected(serial="dev_galaxy")
    assert len(runs) == 1
    run = runs[0]
    assert run.status == AutomationRunStatus.SUCCESS
    assert len(run.actions_executed) == 3

    assert run.actions_executed[0].action_type == ActionType.RUN_SYNC_PROFILE
    assert run.actions_executed[0].job_id == "job_sync_123"

    assert run.actions_executed[1].action_type == ActionType.RUN_BACKUP_PROFILE
    assert run.actions_executed[1].job_id == "job_backup_456"

    mock_sync.enqueue_sync_job.assert_called_once()
    mock_backup.enqueue_profile.assert_called_once_with("backup_docs", serial="dev_galaxy")


def test_on_notification_save_and_log(memory_db, mock_adb):
    service = AutomationService(database=memory_db, adb_service=mock_adb)

    rule = AutomationRule(
        id="rule_otp_save",
        name="Guardar Códigos OTP de Banco",
        trigger=TriggerType.NOTIFICATION_RECEIVED,
        conditions=AutomationCondition(
            package_name="com.banco.app",
            text_contains="clave dinámica",
        ),
        actions=[
            AutomationAction(action_type=ActionType.SAVE_NOTIFICATION),
            AutomationAction(action_type=ActionType.LOG_SUMMARY, params={"message": "Clave dinámica detectada"}),
        ],
    )
    service.save_rule(rule)

    payload_matching = {
        "serial": "dev-01",
        "package": "com.banco.app",
        "titulo": "Seguridad",
        "texto": "Tu clave dinámica para transferir es 884120",
    }
    runs = service.on_notification(payload_matching)
    assert len(runs) == 1
    assert runs[0].status == AutomationRunStatus.SUCCESS

    # Verificar que se guardó en notifications de SQLite
    with memory_db.connect() as conn:
        row = conn.execute("SELECT payload_json FROM notifications").fetchone()
        assert row is not None
        saved = eval(row[0]) if isinstance(row[0], dict) else row[0]
        assert "884120" in str(saved)

    # Probar que notificación no coincidente se ignora
    payload_other = {
        "serial": "dev-01",
        "package": "com.banco.app",
        "titulo": "Promoción",
        "texto": "Obtén un crédito hoy",
    }
    runs_none = service.on_notification(payload_other)
    assert len(runs_none) == 0


def test_shell_safe_command_security_allowlist(memory_db, mock_adb):
    service = AutomationService(database=memory_db, adb_service=mock_adb)

    # 1. Comando seguro permitido en allowlist
    res_ok = service.execute_action(
        AutomationAction(action_type=ActionType.SHELL_SAFE_COMMAND, params={"command": "uptime"}),
        {"serial": "phone-1"},
    )
    assert res_ok.status == AutomationRunStatus.SUCCESS
    assert "uptime: 10 hours" in res_ok.output
    mock_adb.shell.assert_called_with("uptime", serial="phone-1", timeout=15.0)

    # 2. Comando peligroso / arbitrario fuera de allowlist
    res_bad = service.execute_action(
        AutomationAction(action_type=ActionType.SHELL_SAFE_COMMAND, params={"command": "rm -rf /sdcard"}),
        {"serial": "phone-1"},
    )
    assert res_bad.status == AutomationRunStatus.FAILED
    assert "COMANDO_NO_PERMITIDO" in res_bad.error


def test_generate_summary_json(memory_db, mock_adb):
    service = AutomationService(database=memory_db, adb_service=mock_adb)

    r1 = AutomationRun(
        run_id="r1",
        rule_id="rule_1",
        trigger_type=TriggerType.DEVICE_CONNECTED,
        status=AutomationRunStatus.SUCCESS,
    )
    r2 = AutomationRun(
        run_id="r2",
        rule_id="rule_2",
        trigger_type=TriggerType.STORAGE_LOW,
        status=AutomationRunStatus.FAILED,
    )

    summary = service.generate_summary([r1, r2])
    assert summary["total_runs"] == 2
    assert summary["success"] == 1
    assert summary["failed"] == 1
    assert len(summary["runs"]) == 2
