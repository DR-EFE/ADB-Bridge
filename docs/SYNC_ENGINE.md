# Motor de Sincronización Inteligente (Fase 3B)

El motor de sincronización inteligente (`SyncService`) proporciona transferencias incrementales basadas en reglas entre dispositivos Android y la PC local, con detección de colisiones, verificación de integridad SHA-256 e integración con la cola de trabajos reanudables (`JobQueueService`).

---

## 1. Arquitectura y Contratos

```text
               ┌────────────────────────┐
               │      SyncProfile       │
               │  - direction           │
               │  - SyncRule            │
               └───────────┬────────────┘
                           │
                           ▼
                 ┌───────────────────┐
                 │    SyncService    │
                 └─────┬───────┬─────┘
                       │       │
       ┌───────────────┘       └────────────────┐
       ▼                                        ▼
┌──────────────┐                       ┌──────────────────┐
│  AdbService  │ (shell/pull/push)     │  JobQueueService │
└──────────────┘                       └──────────────────┘
       │                                        │
       ▼                                        ▼
 Dispositivo Android                     SQLite (sync_*) + UI
```

### Principios Fundamentales
- **Aislamiento**: No modifica el núcleo ADB (`AndroidAdb.psm1`, `adb_service.py`), ni el subsistema de backups completos (`BackupManifest`).
- **Contrato ADB Único**: Toda interacción remota se realiza mediante `AdbService` con serial explícito (`-s <serial>`).
- **Incrementalidad**: Compara tamaño y checksum SHA-256 (`sha256sum` en remoto y `sha256_file` de `resumable_transfer` en local) para omitir archivos idénticos sin retransferir datos innecesarios.
- **Persistencia Aislada**: Los perfiles y reportes se persisten en SQLite (`android_admin.db`) bajo las tablas `sync_profiles` y `sync_reports`.

---

## 2. Definición de Perfiles y Reglas

### Direcciones Soportadas (`SyncDirection`)
- `phone_to_pc`: Teléfono hacia PC.
- `pc_to_phone`: PC hacia Teléfono.
- `bidirectional`: Sincronización en ambos sentidos con resolución de conflictos.

### Reglas Configurables (`SyncRule`)
| Campo | Tipo | Por Defecto | Descripción |
|---|---|---|---|
| `exclude_patterns` | `List[str]` | `[".*", "*.tmp", "*.part", ...]` | Patrones glob a ignorar. |
| `exclude_dirs` | `List[str]` | `[".conflict", ".trash", "cache"]` | Carpetas a omitir recursivamente. |
| `max_file_size_bytes` | `Optional[int]` | `None` | Límite máximo de tamaño por archivo. |
| `max_total_bytes` | `Optional[int]` | `None` | Límite acumulado por sesión. |
| `preserve_structure` | `bool` | `True` | Mantiene la jerarquía relativa de carpetas. |
| `use_trash` | `bool` | `True` | Mueve archivos reemplazados a `.trash`/`.conflict`. |
| `conflict_strategy` | `ConflictResolution` | `KEEP_BOTH` | Estrategia de resolución de colisiones. |
| `only_new_photos` | `bool` | `False` | Filtra solo multimedia (jpg, png, mp4, etc.). |
| `only_modified_docs` | `bool` | `False` | Filtra solo documentos (pdf, docx, txt, etc.). |

### Estrategias de Conflicto (`ConflictResolution`)
Cuando un archivo existe en ambos extremos y sus contenidos (SHA-256) difieren:
1. `KEEP_BOTH`: Conserva la versión remota y renombra el archivo local con sufijo `_local_<timestamp>`.
2. `KEEP_LOCAL`: Sobrescribe la copia en el teléfono con la versión local de la PC.
3. `KEEP_REMOTE`: Sobrescribe la copia local (archivando la previa en `.conflict/` si `use_trash=True`).
4. `SKIP`: No realiza transferencia y registra el conflicto en el `SyncReport`.

---

## 3. Ejemplo de Uso

### Crear y Guardar un Perfil

```python
from pathlib import Path
from ui.services.sync_profiles import SyncProfile, SyncDirection, SyncRule, ConflictResolution
from ui.services.sync_service import SyncService

service = SyncService()

# 1. Crear perfil personalizado o usar preset
profile = SyncService.create_preset_profile(
    preset_type="new_photos",
    name="Mis Fotos al PC",
    local_path=Path("C:/Users/Usuario/Pictures/Android"),
    remote_path="/sdcard/DCIM/Camera",
    serial="192.168.1.50:5555"
)

# 2. Guardar en SQLite
service.save_profile(profile)
```

### Ejecutar Sincronización Directa

```python
report = service.run_sync(profile)

print(f"Éxito: {report.success}")
print(f"Transferidos: {report.transferred_count} archivos ({report.transferred_bytes} bytes)")
print(f"Omitidos: {report.skipped_count}")
print(f"Conflictos: {report.conflict_count}")
```

### Encolar como Trabajo Reanudable (`JobQueueService`)

```python
from ui.services.job_queue_service import JobQueueService

queue = JobQueueService()
job_id = service.enqueue_sync_job(profile, job_queue=queue)

# Permite pause, resume, cancel desde el JobQueueService
queue.pause(job_id)
queue.resume(job_id)
```

---

## 4. Estructura del SyncReport (JSON)

Cada sincronización genera un reporte estructurado guardado en SQLite (`sync_reports`):

```json
{
  "sync_id": "sync_a1b2c3d4e5f6",
  "profile_id": "photos_to_pc",
  "direction": "phone_to_pc",
  "started_at": "2026-10-21T02:00:00Z",
  "completed_at": "2026-10-21T02:00:15Z",
  "success": true,
  "transferred_count": 12,
  "transferred_bytes": 48234900,
  "skipped_count": 4,
  "skipped_bytes": 1289000,
  "conflict_count": 1,
  "failed_count": 0,
  "conflicts": [
    {
      "relative_path": "Documents/nota.txt",
      "local_size": 1024,
      "remote_size": 1200,
      "resolution": "keep_both",
      "action_taken": "Conservados ambos: local renombrado a nota_local_20261021_020010.txt"
    }
  ],
  "items": [
    {
      "path": "Camera/IMG_001.jpg",
      "status": "transferred",
      "size_bytes": 4019200,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  ],
  "errors": []
}
```
