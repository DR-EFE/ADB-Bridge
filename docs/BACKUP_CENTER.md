# Centro de respaldos

El Centro de respaldos usa perfiles declarativos y el contrato existente de
`AdbService`; no asume permisos que Android no conceda. Contactos, SMS y
llamadas pueden terminar en `DENIED`, `EMPTY` o `PARTIAL` y esos estados se
conservan en el manifiesto.

```python
from pathlib import Path
from ui.services.adb_service import AdbService
from ui.services.backup_service import BackupService

result = BackupService().run_profile(
    "photos_videos",
    serial="192.168.1.50:5555",
    adb_service=AdbService(),
    destination_dir=Path("backups") / "phone-2026-10-20",
)
```

Perfiles disponibles: `photos_videos`, `whatsapp`, `documents`,
`contacts_sms_calls`, `installed_apps` y `custom` (con `custom_folders`).

Cada destino contiene `MANIFEST.json`, `checksums.json`, `errors.json` y
`backup-report.html`. `BackupService.compare_backups()` compara dos
manifiestos por SHA-256. `restore_selected()` restaura rutas seleccionadas o
una categoría mediante `AdbService.push`.

Para trabajo reanudable se usa `enqueue_profile(queue_service, ...)`; la cola
existente aporta pausa, reanudación, cancelación y reintentos.

Para repetir solamente categorías con `DENIED`, `PARTIAL` o `ERROR`, usa
`BackupService.retry_failed_profile(".../MANIFEST.json", ...)` después de
corregir el permiso o la conectividad correspondiente.
