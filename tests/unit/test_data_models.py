from datetime import timezone

from ui.services.adb_models import (
    BackupManifest, Device, DeviceState, OperationResult, TransferJob,
    TransferJobStatus,
)


def test_models_round_trip_json():
    device = Device(serial="192.168.1.20:5555", state=DeviceState.DEVICE)
    assert device.is_network
    raw = device.model_dump_json()
    assert Device.model_validate_json(raw).serial == device.serial

    job = TransferJob(id="job-1", serial=device.serial, source="a", destination="b")
    job.complete()
    restored = TransferJob.model_validate_json(job.model_dump_json())
    assert restored.status == TransferJobStatus.COMPLETED
    assert restored.progress == 1


def test_backup_manifest_complete_requires_no_errors():
    manifest = BackupManifest(id="backup-1", serial="usb-1", destination="out")
    assert not manifest.complete
    manifest.status = TransferJobStatus.COMPLETED
    assert manifest.complete
    manifest.error_count = 1
    assert not manifest.complete

