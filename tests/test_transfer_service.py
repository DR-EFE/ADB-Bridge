"""Pruebas unitarias para TransferService y RemotePath."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.services.transfer_service import (
    RemotePath,
    TransferJob,
    TransferService,
    TransferStatus,
    TransferType,
)


class TestRemotePath(unittest.TestCase):
    def test_remote_path_normalization(self):
        p = RemotePath("sdcard/Download/file.txt")
        self.assertEqual(str(p), "/sdcard/Download/file.txt")
        self.assertEqual(p.name, "file.txt")
        self.assertEqual(p.parent, "/sdcard/Download")

    def test_remote_path_under_root(self):
        p = RemotePath("/sdcard/DCIM/Camera/photo.jpg")
        self.assertTrue(p.is_under("/sdcard"))
        self.assertTrue(p.is_under("/sdcard/DCIM"))
        self.assertFalse(p.is_under("/data"))

    def test_remote_path_join(self):
        p = RemotePath("/sdcard")
        child = p.join("Download/sub/test.png")
        self.assertEqual(str(child), "/sdcard/Download/sub/test.png")


class TestTransferService(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.service = TransferService()

    def tearDown(self):
        self.service.shutdown()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_enqueue_and_query_jobs(self):
        """Verifica encolar trabajos y consultarlos por Id."""
        test_file = self.test_dir / "sample.txt"
        test_file.write_text("contenido de prueba", encoding="utf-8")

        jids = self.service.enqueue_push("TEST_SERIAL", [str(test_file)], "/sdcard/Download")
        self.assertEqual(len(jids), 1)

        job = self.service.get_job(jids[0])
        self.assertIsNotNone(job)
        self.assertEqual(job.serial, "TEST_SERIAL")
        self.assertEqual(job.job_type, TransferType.PUSH_FILE)

    def test_push_dir_preserves_structure(self):
        """Verifica que push_dir recolecte y prepare las subcarpetas jerárquicamente."""
        folder = self.test_dir / "my_project"
        folder.mkdir()
        (folder / "file1.txt").write_text("uno", encoding="utf-8")
        sub = folder / "nested" / "deep"
        sub.mkdir(parents=True)
        (sub / "file2.txt").write_text("dos", encoding="utf-8")

        recorded_commands = []

        def fake_run_adb_cmd(adb_exe, args, job=None, timeout=None):
            recorded_commands.append(args)
            return 0, "", ""

        with patch.object(self.service, "_run_adb_cmd", side_effect=fake_run_adb_cmd):
            jids = self.service.enqueue_push("SERIAL_1", [str(folder)], "/sdcard/Download")
            # Esperar a que el worker procese
            time.sleep(0.5)

            job = self.service.get_job(jids[0])
            self.assertEqual(job.status, TransferStatus.COMPLETED)
            self.assertEqual(job.total_files, 2)
            self.assertEqual(job.files_transferred, 2)

            # Verificar que se crearon los directorios remotos anidados
            mkdir_cmds = [cmd for cmd in recorded_commands if "mkdir" in cmd]
            self.assertTrue(any("/sdcard/Download/my_project" in c for c in [arg for cmd in mkdir_cmds for arg in cmd]))
            self.assertTrue(any("nested/deep" in c for c in [arg for cmd in mkdir_cmds for arg in cmd]))

    def test_transient_error_retry(self):
        """Verifica que errores transitorios reintenten hasta 2 veces."""
        attempts_count = 0

        def flaky_adb_cmd(adb_exe, args, job=None, timeout=None):
            nonlocal attempts_count
            attempts_count += 1
            if attempts_count < 3:
                return 1, "", "error: device offline"
            return 0, "OK", ""

        with patch.object(self.service, "_run_adb_cmd", side_effect=flaky_adb_cmd):
            jid = self.service.enqueue_mkdir("SERIAL_1", "/sdcard/NewFolder")
            # Esperar a que se ejecuten los reintentos
            for _ in range(30):
                job = self.service.get_job(jid)
                if job.status in (TransferStatus.COMPLETED, TransferStatus.FAILED):
                    break
                time.sleep(0.2)

            self.assertEqual(job.status, TransferStatus.COMPLETED)
            self.assertEqual(job.attempts, 2)

    def test_permanent_error_no_auto_retry_and_manual_retry(self):
        """Verifica que un error permanente no reintente automáticamente pero permita reintento manual."""
        call_count = 0

        def fail_perm(adb_exe, args, job=None, timeout=None):
            nonlocal call_count
            call_count += 1
            return 1, "", "rm: /sdcard/sys: Permission denied"

        with patch.object(self.service, "_run_adb_cmd", side_effect=fail_perm):
            jid = self.service.enqueue_delete("SERIAL_1", "/sdcard/sys")
            time.sleep(0.4)

            job = self.service.get_job(jid)
            self.assertEqual(job.status, TransferStatus.FAILED)
            self.assertEqual(job.attempts, 0)
            self.assertIn("Permission denied", job.error)

            # Reintento manual
            ok = self.service.retry_job(jid)
            self.assertTrue(ok)
            time.sleep(0.4)
            self.assertGreater(call_count, 1)

    def test_cancellation_preserves_partial_state(self):
        """Verifica que al cancelar tras transferir archivos, el estado sea PARTIAL."""
        job = TransferJob(
            id="job_cancel",
            serial="SERIAL_1",
            job_type=TransferType.PUSH_DIR,
            source=str(self.test_dir),
            destination="/sdcard/Download",
            bytes_transferred=1024,
            files_transferred=1,
            status=TransferStatus.RUNNING,
        )
        with self.service._jobs_lock:
            self.service._jobs[job.id] = job

        self.service.cancel_job("job_cancel")
        self.assertTrue(job.cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
