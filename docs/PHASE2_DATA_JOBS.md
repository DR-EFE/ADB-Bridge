# Fase 2: datos y trabajos

Los contratos persistibles son `Device`, `ConnectionAttempt`, `OperationResult`,
`TransferJob` y `BackupManifest` en `ui/services/adb_models.py`. Los objetos de
runtime como `threading.Event` y `Popen` quedan fuera de los modelos JSON.

## SQLite

`SQLiteStore` en `ui/services/database.py` es la fuente de verdad. Por defecto
usa `data/android_admin.db`; se puede cambiar con `ANDROID_TOOLS_DATA_DIR` o
pasando `SQLiteStore(path)`. Inicialización:

```powershell
python .\scripts\init_database.py
```

El esquema incluye dispositivos, intentos de conexión, jobs, backups,
notificaciones, errores y métricas.

## Jobs reanudables

`JobQueueService` conserva la compatibilidad con la GUI y añade `pause()`,
`resume()` y `wait_if_paused()`. El payload conserva el checkpoint de bytes,
archivos, checksums SHA-256 y archivos fallidos. `resumable_transfer.py`
construye planes, detecta archivos sin cambios y verifica el hash después de
copiar.

```python
from ui.services.adb_models import TransferJob

job = TransferJob(id="job-1", serial="device-1", source="entrada",
                  destination="salida")
job.complete()

failed = TransferJob(id="job-2", serial="device-1", source="entrada",
                     destination="salida")
failed.fail("origen no disponible")
```
