import json
import threading
import time
from pathlib import Path

from ui.services.backup_profiles import get_backup_profiles
from ui.services.backup_service import BackupService
from ui.services.job_queue_service import JobQueueService, JobStatus


class Result:
    def __init__(self, success=True, output="ok", message=""):
        self.success = success
        self.output = output
        self.message = message


class FakeAdb:
    def __init__(self):
        self.pushed = []

    def pull(self, remote_path, local_path, **kwargs):
        target = Path(local_path) / "copied.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(remote_path, encoding="utf-8")
        return Result()

    def shell(self, command, **kwargs):
        if "content://sms" in command:
            return Result(False, "", "SecurityException: Permission Denial")
        return Result(True, "package:example")

    def push(self, local_path, remote_path, **kwargs):
        self.pushed.append((local_path, remote_path))
        return Result()


def test_profiles_cover_required_center_categories():
    profiles = get_backup_profiles(["MyFolder"])
    assert {"photos_videos", "whatsapp", "documents", "contacts_sms_calls", "installed_apps", "custom"} <= set(profiles)


def test_profile_generates_all_artifacts_and_honest_permission_status(tmp_path):
    output = BackupService().run_profile(
        "contacts_sms_calls", serial="serial-1", adb_service=FakeAdb(), destination_dir=tmp_path,
    )
    for name in ("MANIFEST.json", "checksums.json", "errors.json", "backup-report.html"):
        assert (tmp_path / name).is_file()
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["Profile"] == "contacts_sms_calls"
    assert next(item for item in manifest["Categories"] if item["Id"] == "sms")["Status"] == "DENIED"
    assert output["OverallStatus"] == "PARTIAL"


def test_compare_and_selective_restore(tmp_path):
    adb = FakeAdb()
    first = tmp_path / "first"
    second = tmp_path / "second"
    service = BackupService()
    service.run_profile("documents", serial="serial-1", adb_service=adb, destination_dir=first)
    service.run_profile("documents", serial="serial-1", adb_service=adb, destination_dir=second)
    extra = second / "download" / "extra.txt"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("new", encoding="utf-8")
    # Rebuild second manifest with the additional file through the profile's artifact helper.
    service.run_profile("documents", serial="serial-1", adb_service=adb, destination_dir=second)
    diff = service.compare_backups(first / "MANIFEST.json", second / "MANIFEST.json")
    assert isinstance(diff.added, list)
    manifest = json.loads((first / "MANIFEST.json").read_text(encoding="utf-8"))
    selected = manifest["Files"][0]["path"]
    restored = service.restore_selected(first / "MANIFEST.json", adb_service=adb, serial="serial-1", paths=[selected])
    assert restored["success"]
    assert len(adb.pushed) == 1


def test_profile_can_run_through_resumable_job_queue(tmp_path):
    queue = JobQueueService(history_path=tmp_path / "jobs.json", database_path=tmp_path / "jobs.db")
    completed = threading.Event()
    queue.add_listener(lambda job: completed.set() if job.status == JobStatus.COMPLETED else None)
    try:
        job_id = BackupService().enqueue_profile(
            queue, "documents", serial="serial-1", adb_service=FakeAdb(), destination_dir=tmp_path / "backup",
        )
        assert completed.wait(2)
        assert queue.get_job(job_id).status == JobStatus.COMPLETED
    finally:
        queue.shutdown()
