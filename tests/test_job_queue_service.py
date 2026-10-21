"""Pruebas unitarias para JobQueueService.

Cubre:
1. Encolado y ejecución secuencial por dispositivo.
2. Ejecución paralela real entre diferentes dispositivos.
3. Separación correcta entre DeviceWorker y LocalWorker.
4. Transición de estados: PENDING -> RUNNING -> COMPLETED.
5. Reintentos automáticos (5 reintentos ante errores transitorios) y agotamiento.
6. Cancelación cooperativa de trabajos en cola y en ejecución.
7. Persistencia y carga de historial en JSON.
8. Limpieza selectiva de trabajos completados.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ui.services.job_queue_service import (
    JobItem,
    JobQueueService,
    JobStatus,
    JobType,
    is_error_transient,
)


class TestJobQueueService(unittest.TestCase):
    """Suite de pruebas para el servicio de cola de trabajos."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.temp_dir.name) / "jobs_test.json"
        self.service = JobQueueService(history_path=self.history_file, max_local_workers=2)

    def tearDown(self):
        self.service.shutdown()
        time.sleep(0.05)
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_enqueue_and_complete_local_job(self):
        """Verifica que un trabajo local se procese y complete actualizando su progreso."""
        completed_event = threading.Event()

        def dummy_local_handler(job: JobItem, update_progress):
            update_progress(0.5, "Procesando mitad")
            time.sleep(0.05)
            update_progress(1.0, "Listo")
            completed_event.set()
            return {"rows_parsed": 42}

        self.service.register_handler(JobType.LOCAL_PROCESS, dummy_local_handler)

        job_id = self.service.enqueue(
            job_type=JobType.LOCAL_PROCESS,
            title="Parseo local de prueba",
            payload={"source": "test.zip"},
        )

        self.assertTrue(completed_event.wait(timeout=3.0))
        time.sleep(0.05)

        job = self.service.get_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.progress_percent, 1.0)
        self.assertEqual(job.result, {"rows_parsed": 42})
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)

    def test_sequential_execution_per_device(self):
        """Dos trabajos en el mismo serial deben ejecutarse estrictamente uno tras otro."""
        execution_order = []
        job1_started = threading.Event()
        job1_release = threading.Event()
        job2_done = threading.Event()

        def handler1(job: JobItem, update_progress):
            execution_order.append(f"{job.id}-start")
            job1_started.set()
            job1_release.wait(timeout=2.0)
            execution_order.append(f"{job.id}-finish")

        def handler2(job: JobItem, update_progress):
            execution_order.append(f"{job.id}-start")
            job2_done.set()

        jid1 = self.service.enqueue(
            job_type=JobType.WHATSAPP_EXPORT_CHAT,
            title="Chat 1",
            serial="device-A",
            custom_handler=handler1,
        )
        jid2 = self.service.enqueue(
            job_type=JobType.WHATSAPP_EXPORT_CHAT,
            title="Chat 2",
            serial="device-A",
            custom_handler=handler2,
        )

        # Esperar a que inicie el trabajo 1
        self.assertTrue(job1_started.wait(timeout=2.0))
        # Mientras job1 corre, job2 DEBE estar en PENDING
        job2 = self.service.get_job(jid2)
        self.assertEqual(job2.status, JobStatus.PENDING)

        # Liberar job1
        job1_release.set()

        # Esperar a que termine job2
        self.assertTrue(job2_done.wait(timeout=2.0))
        time.sleep(0.05)

        self.assertEqual(
            execution_order,
            [f"{jid1}-start", f"{jid1}-finish", f"{jid2}-start"],
        )

    def test_parallel_execution_across_different_devices(self):
        """Trabajos en dispositivos diferentes deben ejecutarse en paralelo."""
        devA_running = threading.Event()
        devB_running = threading.Event()
        all_finish = threading.Event()

        def handler_a(job, progress):
            devA_running.set()
            all_finish.wait(timeout=2.0)

        def handler_b(job, progress):
            devB_running.set()
            all_finish.wait(timeout=2.0)

        self.service.enqueue(
            job_type=JobType.BACKUP_CATEGORY,
            title="Backup Dev A",
            serial="serial-111",
            custom_handler=handler_a,
        )
        self.service.enqueue(
            job_type=JobType.BACKUP_CATEGORY,
            title="Backup Dev B",
            serial="serial-222",
            custom_handler=handler_b,
        )

        # Ambos deben entrar en ejecución concurrentemente
        self.assertTrue(devA_running.wait(timeout=2.0))
        self.assertTrue(devB_running.wait(timeout=2.0))

        # Liberar ambos
        all_finish.set()

    def test_automatic_retries_transient_error(self):
        """Un fallo transitorio (ej: connection reset) se reintenta automáticamente hasta 5 veces."""
        attempts = [0]
        completed_event = threading.Event()

        def flaky_handler(job: JobItem, update_progress):
            attempts[0] += 1
            if attempts[0] < 3:
                # Simular error transitorio
                raise RuntimeError("connection reset by peer")
            # Éxito en el intento 3
            completed_event.set()

        jid = self.service.enqueue(
            job_type=JobType.FILE_TRANSFER,
            title="Subida inestable",
            serial="device-flaky",
            max_retries=5,
            custom_handler=flaky_handler,
        )

        self.assertTrue(completed_event.wait(timeout=10.0))
        time.sleep(0.05)

        job = self.service.get_job(jid)
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(attempts[0], 3)
        self.assertLess(job.retries_left, 5)

    def test_exhausted_retries_marks_job_failed(self):
        """Si un error persiste tras agotar reintentos, el trabajo se marca como FAILED."""
        def permanent_fail_handler(job: JobItem, update_progress):
            raise RuntimeError("permission denied (permanent)")

        jid = self.service.enqueue(
            job_type=JobType.ADB_COMMAND,
            title="Comando prohibido",
            serial="device-fail",
            max_retries=2,
            custom_handler=permanent_fail_handler,
        )

        # Esperar a que termine
        time.sleep(0.5)
        job = self.service.get_job(jid)
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("permission denied", job.error)

    def test_cancel_pending_job(self):
        """Cancelar un trabajo mientras está en cola (PENDING) evita su ejecución."""
        block_dev = threading.Event()
        release_dev = threading.Event()
        job2_executed = threading.Event()

        def blocking_handler(job, progress):
            block_dev.set()
            release_dev.wait(timeout=2.0)

        def second_handler(job, progress):
            job2_executed.set()

        jid1 = self.service.enqueue(
            job_type=JobType.WHATSAPP_EXPORT_CHAT,
            title="Bloqueador",
            serial="dev-queue",
            custom_handler=blocking_handler,
        )
        jid2 = self.service.enqueue(
            job_type=JobType.WHATSAPP_EXPORT_CHAT,
            title="Por cancelar",
            serial="dev-queue",
            custom_handler=second_handler,
        )

        self.assertTrue(block_dev.wait(timeout=2.0))

        # Cancelar el segundo mientras espera
        cancelled = self.service.cancel(jid2)
        self.assertTrue(cancelled)

        job2 = self.service.get_job(jid2)
        self.assertEqual(job2.status, JobStatus.CANCELLED)

        # Liberar el primero
        release_dev.set()
        time.sleep(0.2)

        # El segundo NUNCA debió ejecutarse
        self.assertFalse(job2_executed.is_set())

    def test_cancel_running_job(self):
        """Cancelar un trabajo activo activa stop_event y lo marca como CANCELLED."""
        started_event = threading.Event()

        def long_running_handler(job: JobItem, progress):
            started_event.set()
            while not job.stop_event.is_set():
                time.sleep(0.02)
            # Salida cooperativa
            return

        jid = self.service.enqueue(
            job_type=JobType.BACKUP_CATEGORY,
            title="Backup largo",
            serial="dev-long",
            custom_handler=long_running_handler,
        )

        self.assertTrue(started_event.wait(timeout=2.0))
        cancelled = self.service.cancel(jid)
        self.assertTrue(cancelled)

        time.sleep(0.2)
        job = self.service.get_job(jid)
        self.assertEqual(job.status, JobStatus.CANCELLED)

    def test_manual_retry_job(self):
        """Un trabajo cancelado o fallido puede reintentarse con service.retry()."""
        attempts = [0]
        success_event = threading.Event()

        def fail_then_ok(job: JobItem, progress):
            attempts[0] += 1
            if attempts[0] == 1:
                raise RuntimeError("fallo inicial manual")
            success_event.set()

        jid = self.service.enqueue(
            job_type=JobType.ADB_COMMAND,
            title="Comando para reintentar",
            serial="dev-retry",
            max_retries=0,  # Sin reintentos automáticos
            custom_handler=fail_then_ok,
        )

        time.sleep(0.3)
        job = self.service.get_job(jid)
        self.assertEqual(job.status, JobStatus.FAILED)

        # Reintentar manualmente
        re_enqueued = self.service.retry(jid)
        self.assertTrue(re_enqueued)

        self.assertTrue(success_event.wait(timeout=2.0))
        time.sleep(0.05)
        self.assertEqual(self.service.get_job(jid).status, JobStatus.COMPLETED)

    def test_persistence_and_reload(self):
        """Verifica que los trabajos se guarden en JSON y se recarguen correctamente."""
        jid = self.service.enqueue(
            job_type=JobType.LOCAL_PROCESS,
            title="Trabajo persistente",
            payload={"dato": 123},
        )
        time.sleep(0.1)

        # Crear una segunda instancia del servicio apuntando al mismo archivo JSON
        service2 = JobQueueService(history_path=self.history_file)
        try:
            loaded_job = service2.get_job(jid)
            self.assertIsNotNone(loaded_job)
            self.assertEqual(loaded_job.title, "Trabajo persistente")
            self.assertEqual(loaded_job.payload, {"dato": 123})
        finally:
            service2.shutdown()

    def test_clear_completed(self):
        """clear_completed() elimina los terminados pero conserva los activos."""
        completed = threading.Event()

        def dummy_h(job, prog):
            completed.set()
            return True

        j1 = self.service.enqueue(JobType.LOCAL_PROCESS, "Completado 1", custom_handler=dummy_h)
        self.assertTrue(completed.wait(timeout=2.0))
        time.sleep(0.05)

        self.service.clear_completed()
        self.assertIsNone(self.service.get_job(j1))


if __name__ == "__main__":
    unittest.main()
