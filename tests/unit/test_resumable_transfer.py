from ui.services.adb_models import TransferJob, TransferJobStatus
from ui.services.resumable_transfer import build_transfer_plan, execute_local_plan


def test_transfer_plan_skips_unchanged_file(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "a.txt").write_text("same", encoding="utf-8")
    destination.mkdir()
    (destination / "a.txt").write_text("same", encoding="utf-8")
    plan = build_transfer_plan(source, destination, only_changes=True)
    assert len(plan) == 1
    assert plan[0].skip


def test_execute_plan_records_checksum_and_completion(tmp_path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "out"
    source.write_bytes(b"hello")
    job = TransferJob(id="job-1", serial="dev-1", source=str(source), destination=str(destination))
    result = execute_local_plan(job, build_transfer_plan(source, destination))
    assert result.status == TransferJobStatus.COMPLETED
    assert result.files_completed == 1
    assert (destination / source.name).read_bytes() == b"hello"
    assert len(result.checksums) == 1


def test_execute_plan_can_be_cancelled_without_copying_everything(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "out"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "b.txt").write_text("b", encoding="utf-8")
    job = TransferJob(id="job-2", serial="dev-1", source=str(source), destination=str(destination))
    result = execute_local_plan(job, build_transfer_plan(source, destination), cancel=lambda: True)
    assert result.status == TransferJobStatus.CANCELLED

