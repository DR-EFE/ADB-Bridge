import json

from ui.services.database import SQLiteStore


def test_sqlite_store_persists_jobs_and_records(tmp_path):
    store = SQLiteStore(tmp_path / "android.db")
    payload = {"id": "job-1", "serial": "dev-1", "status": "completed", "created_at": "now"}
    store.save_job(payload)
    assert store.list_jobs("completed")[0]["id"] == "job-1"
    store.record_error("test error", {"code": "E1"}, job_id="job-1")
    store.record_metric("jobs.completed", 1)
    store.record_notification({"title": "OTP"}, "dev-1")

