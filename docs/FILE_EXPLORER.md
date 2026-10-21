# Servicio de Exploración de Archivos Avanzado (Fase 5B)

El módulo `FileExplorerService` provee una capa desacoplada y robusta para la navegación del sistema de archivos de dispositivos Android conectados vía ADB, incorporando listado detallado, exploración en árbol, búsqueda filtrada y operaciones masivas seguras.

---

## 1. Arquitectura y Modelos

El servicio se encuentra en `ui/services/file_explorer_service.py` y sus contratos tipados en `ui/services/file_explorer_models.py`.

### Modelos Principales

- **`FileNode`**:
  - `path`: Ruta absoluta remota en Android (ej: `/sdcard/DCIM/Camera/IMG_01.jpg`).
  - `name`: Nombre del archivo o directorio.
  - `is_dir`: Booleano que indica si es carpeta.
  - `size`: Tamaño en bytes (0 para carpetas).
  - `mtime`: `datetime` de última modificación si está disponible.
  - `is_link` y `link_target`: Información sobre enlaces simbólicos.
  - `permissions`: Permisos UNIX (ej: `drwxrwx--x`).
  - `children`: Lista de nodos hijos (utilizado en la vista en árbol).
  - Propiedades auxiliares: `extension` (ej: `.jpg`), `formatted_size` (ej: `1.45 MB`, `<DIR>`).

- **`ExplorerFilter`**:
  - `name_glob`: Patrón comodín (ej: `*.pdf`, `IMG_*`).
  - `extensions`: Lista de extensiones permitidas (ej: `['.jpg', '.png']`).
  - `min_size` y `max_size`: Umbrales de tamaño en bytes.
  - `modified_after` y `modified_before`: Filtros temporales.
  - `only_dirs` / `only_files`: Restricción por tipo de nodo.

- **`SearchResult`**:
  - `root`: Ruta base de búsqueda.
  - `total_found`: Total de elementos coincidentes.
  - `items`: Lista de `FileNode`.
  - `truncated`: `True` si se alcanzó el límite `max_results`.
  - `duration_seconds`: Tiempo de ejecución.

- **`BulkOperationResult`**:
  - `operation`: `BulkOperationType.DOWNLOAD`, `UPLOAD`, `DELETE`, `TRASH`.
  - `serial`: Serial explícito del dispositivo.
  - `total_items`, `succeeded_items`, `failed_items`: Métricas de ejecución.
  - `results`: Detalle por cada archivo procesado (`BulkItemResult`).
  - `job_id`: ID del trabajo en `JobQueueService` si fue encolado.

---

## 2. Ejemplos de Uso

### Ejemplo 1: Listado de Directorio (`list_dir`)

```python
from ui.services.adb_service import AdbService
from ui.services.file_explorer_service import FileExplorerService

adb = AdbService()
explorer = FileExplorerService(adb_service=adb)

# Serial explícito obligatorio
serial = "192.168.1.100:5555"
nodes = explorer.list_dir("/sdcard/Download", serial=serial)

for node in nodes:
    tipo = "[DIR] " if node.is_dir else "[FILE]"
    print(f"{tipo} {node.name.ljust(30)} {node.formatted_size.rjust(10)} (Modificado: {node.mtime})")
```

### Ejemplo 2: Exploración en Árbol Acotado (`tree`)

Para evitar sobrecargar memoria o la interfaz, la profundidad del árbol está limitada (`max_depth`, por defecto 2, máximo 5):

```python
# Obtener árbol de /sdcard/Documents hasta 2 niveles de profundidad
tree_root = explorer.tree(
    path="/sdcard/Documents",
    serial=serial,
    max_depth=2,
)

def print_tree(node, indent=0):
    prefix = "  " * indent + ("📁 " if node.is_dir else "📄 ")
    print(f"{prefix}{node.name} ({node.formatted_size})")
    if node.children:
        for child in node.children:
            print_tree(child, indent + 1)

print_tree(tree_root)
```

### Ejemplo 3: Búsqueda con Filtros (`search`)

Búsqueda optimizada utilizando `find` de Toybox con límite de resultados y control de tiempo:

```python
from datetime import datetime
from ui.services.file_explorer_models import ExplorerFilter

# Buscar imágenes mayores a 1 MB modificadas este año
filtro = ExplorerFilter(
    name_glob="IMG_*",
    extensions=[".jpg", ".png"],
    min_size=1024 * 1024, # >= 1 MB
    modified_after=datetime(2026, 1, 1),
)

resultado = explorer.search(
    root="/sdcard/DCIM",
    filter=filtro,
    serial=serial,
    max_results=50,
)

print(f"Encontrados: {resultado.total_found} (Truncado: {resultado.truncated}) en {resultado.duration_seconds:.2f}s")
for item in resultado.items:
    print(f"- {item.path} ({item.formatted_size})")
```

### Ejemplo 4: Estadísticas de Almacenamiento de Carpeta (`folder_stat`)

Calcula el tamaño aproximado de una carpeta remota mediante `du -sk`:

```python
stat = explorer.folder_stat("/sdcard/WhatsApp", serial=serial)
if stat.supported:
    mb = (stat.total_size_bytes or 0) / (1024 * 1024)
    print(f"Tamaño total de WhatsApp: {mb:.2f} MB")
else:
    print(f"Comando du no soportado o denegado: {stat.message}")
```

### Ejemplo 5: Descarga Masiva Directa o Encolada (`download_paths`)

```python
remote_files = [
    "/sdcard/Download/documento1.pdf",
    "/sdcard/Download/documento2.pdf",
    "/sdcard/DCIM/Camera/IMG_20260315.jpg",
]

# Opción A: Descarga síncrona directa
res_direct = explorer.download_paths(
    serial=serial,
    remote_paths=remote_files,
    local_dir="C:/Users/Desarrollo/Downloads/AndroidSync",
)
print(f"Descargados con éxito: {res_direct.succeeded_items}/{res_direct.total_items}")

# Opción B: Encolar en segundo plano vía JobQueueService
from ui.services.job_queue_service import JobQueueService
job_queue = JobQueueService()
explorer_queued = FileExplorerService(adb_service=adb, job_queue_service=job_queue)

res_queue = explorer_queued.download_paths(
    serial=serial,
    remote_paths=remote_files,
    local_dir="C:/Users/Desarrollo/Downloads/AndroidSync",
    enqueue_in_job_queue=True,
)
print(f"Descarga encolada con Job ID: {res_queue.job_id}")
```

### Ejemplo 6: Subida Masiva (`upload_paths`)

```python
local_files = [
    "C:/MisDocumentos/presentacion.pptx",
    "C:/MisDocumentos/balance.xlsx",
]

res_upload = explorer.upload_paths(
    serial=serial,
    local_paths=local_files,
    remote_dir="/sdcard/Documents",
)
print(f"Subidos: {res_upload.succeeded_items}/{res_upload.total_items}")
```

### Ejemplo 7: Eliminación Segura y Papelera Remota (`delete_remote`)

Por defecto, la eliminación **no** ejecuta `rm -rf` destructivo; en su lugar, traslada los elementos hacia la papelera remota (`/sdcard/.trash/`) con una estampa de tiempo para evitar pérdida irreversible de datos:

```python
# Eliminación segura (mueve a /sdcard/.trash/<nombre>_<timestamp>)
res_trash = explorer.delete_remote(
    serial=serial,
    remote_paths=["/sdcard/Download/archivo_temporal.zip"],
    move_to_trash=True, # Valor por defecto
)
for item in res_trash.results:
    print(f"Origen: {item.source} -> Papelera: {item.destination}")

# Eliminación permanente (requiere bandera explícita)
res_perm = explorer.delete_remote(
    serial=serial,
    remote_paths=["/sdcard/Download/basura.tmp"],
    move_to_trash=False,
    confirm_permanent_delete=True, # OBLIGATORIO si move_to_trash es False
)
```

---

## 3. Seguridad y Protección contra Rutas Críticas

El servicio bloquea preventivamente cualquier operación masiva sobre rutas del sistema (`/`, `/system`, `/vendor`, `/etc`, `/proc`, `/sys`, `/data`):

```python
# Esto lanzará ValueError("OPERACION_PELIGROSA") para prevenir accidentes
explorer.download_paths(serial=serial, remote_paths=["/system/bin/sh"], local_dir="C:/tmp")

# Para autorizarlo explícitamente se debe enviar confirm_root_access=True:
explorer.download_paths(
    serial=serial,
    remote_paths=["/system/bin/sh"],
    local_dir="C:/tmp",
    confirm_root_access=True,
)
```
