import threading
import time

from ui.services.job_queue_service import JobQueueService, JobStatus, JobType


def test_pause_and_resume_preserves_job_checkpoint(tmp_path):
    service = JobQueueService(history_path=tmp_path / "jobs.json", database_path=tmp_path / "jobs.db")
    started = threading.Event()
    finished = threading.Event()
    checkpoint = threading.Event()

    def handler(job, progress):
        started.set()
        job.bytes_total = 10
        job.bytes_completed = 5
        progress(0.5, "mitad")
        checkpoint.wait(2)
        service.wait_if_paused(job)
        finished.set()

    service.register_handler(JobType.FILE_TRANSFER, handler)
    job_id = service.enqueue(JobType.FILE_TRANSFER, "transfer", serial="dev-1")
    assert started.wait(2)
    assert service.pause(job_id)
    assert service.get_job(job_id).status == JobStatus.PAUSED
    checkpoint.set()
    assert service.resume(job_id)
    assert finished.wait(2)
    time.sleep(0.05)
    assert service.get_job(job_id).status == JobStatus.COMPLETED
    service.shutdown()
