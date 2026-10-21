# API Local y WebSockets (Fase 6B)

El backend local de FastAPI (`backend/`) expone una API REST y un canal WebSocket de eventos en tiempo real para controlar y supervisar operaciones ADB (respaldos, sincronizaciones, explorador de archivos, diagnósticos de salud y control de la cola de trabajos) desde la misma máquina o clientes autorizados.

---

## 1. Arranque y Configuración de Red

### Arranque Local

Por defecto, el servidor se enlaza exclusivamente a la interfaz local `127.0.0.1` en el puerto `8000`:

```bash
# Opción 1: Ejecución directa del módulo
python -m backend.main

# Opción 2: Usando uvicorn
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Documentación interactiva disponible en:
- **Swagger UI**: `http://127.0.0.1:8000/docs`
- **ReDoc**: `http://127.0.0.1:8000/redoc`

---

## 2. Advertencia de Seguridad y Riesgo de Enlace a la LAN

> [!CAUTION]
> **RIESGO CRÍTICO AL ABRIR A LA LAN (`0.0.0.0`)**:
> Por diseño y seguridad, la API **SOLO** debe ejecutarse en `127.0.0.1`.
> Si se cambia el parámetro `--host 0.0.0.0` para permitir acceso desde otros dispositivos de la red local sin autenticación ni túnel seguro (como HTTPS, VPN o reverse proxy con auth):
> 1. **Acceso no autorizado al dispositivo**: Cualquier usuario o máquina en la red WiFi/LAN podría leer, modificar, transferir o eliminar archivos del teléfono Android conectado.
> 2. **Extracción de datos privados**: Se podrían consultar snapshots de salud, historial de llamadas, contactos o SMS respaldados.
> 3. **Ejecución de trabajos arbitrarios**: Cualquier cliente de la LAN podría saturar la cola de transferencias o reiniciar el dispositivo.

Si requiere acceso remoto seguro, configure una conexión SSH con port forwarding (`ssh -L 8000:localhost:8000 usuario@servidor`) o un proxy inverso local con TLS y autenticación mutua.

---

## 3. Endpoints REST y Ejemplos con `curl`

### 3.1. Estado de la API

#### `GET /`
Comprueba que el backend esté en línea y devuelve los endpoints soportados.

```bash
curl -X GET "http://127.0.0.1:8000/"
```

**Respuesta (200 OK):**
```json
{
  "name": "Android Management Local API",
  "version": "1.0.0",
  "status": "online",
  "docs": "/docs",
  "endpoints": [
    "/api/devices",
    "/api/devices/{serial}/health",
    "/api/backups/profiles",
    "/api/backups/enqueue",
    "/api/sync/profiles",
    "/api/sync/enqueue",
    "/api/jobs",
    "/api/explorer/list",
    "/ws/jobs",
    "/ws/jobs/{job_id}"
  ]
}
```

---

### 3.2. Dispositivos y Diagnóstico de Salud

#### `GET /api/devices`
Lista todos los dispositivos conectados mediante ADB y su estado.

```bash
curl -X GET "http://127.0.0.1:8000/api/devices"
```

**Respuesta (200 OK):**
```json
[
  {
    "serial": "192.168.1.100:5555",
    "state": "device",
    "model": "Pixel_8",
    "usb_serial": null,
    "is_ready": true,
    "is_network": true,
    "friendly_name": "Mi Pixel 8",
    "battery_level": 82,
    "android_version": "14"
  }
]
```

#### `GET /api/devices/{serial}/health`
Recopila un snapshot de diagnóstico del dispositivo: batería, almacenamiento, memoria RAM, versión y latencia ADB.

```bash
curl -X GET "http://127.0.0.1:8000/api/devices/192.168.1.100:5555/health"
```

**Respuesta (200 OK):**
```json
{
  "serial": "192.168.1.100:5555",
  "timestamp": "2026-10-20T21:45:00+00:00",
  "overall_status": "ok",
  "battery_level": 82,
  "battery_status": "charging",
  "temperature_celsius": 31.5,
  "storage_total_bytes": 128000000000,
  "storage_used_bytes": 45000000000,
  "storage_free_bytes": 83000000000,
  "ram_total_bytes": 8589934592,
  "ram_available_bytes": 4294967296,
  "android_version": "14",
  "manufacturer": "Google",
  "model": "Pixel_8",
  "adb_latency_ms": 2.45
}
```

**Errores esperados:**
- `400 Bad Request`: Serial vacío o no válido.
- `404 Not Found`: El dispositivo no figura en ADB.
- `409 Conflict`: El dispositivo no está en estado `device` (ej: `offline` o `unauthorized`).

---

### 3.3. Centro de Respaldos

#### `GET /api/backups/profiles`
Lista los perfiles disponibles para respaldo (fotos, WhatsApp, documentos, etc.).

```bash
curl -X GET "http://127.0.0.1:8000/api/backups/profiles"
```

#### `POST /api/backups/enqueue`
Encola la ejecución de un perfil de respaldo en la cola de trabajos.

```bash
curl -X POST "http://127.0.0.1:8000/api/backups/enqueue" \
     -H "Content-Type: application/json" \
     -d '{
       "serial": "192.168.1.100:5555",
       "profile_id": "photos_videos",
       "destination_dir": "C:/AndroidBackups/Pixel8"
     }'
```

**Respuesta (202 Accepted):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "pending",
  "profile_id": "photos_videos",
  "serial": "192.168.1.100:5555",
  "destination_dir": "C:/AndroidBackups/Pixel8",
  "message": "Respaldo 'Fotos y videos' encolado exitosamente con ID job_a1b2c3d4e5f6."
}
```

---

### 3.4. Sincronización Inteligente

#### `GET /api/sync/profiles`
Lista los perfiles de sincronización incremental persistidos.

```bash
curl -X GET "http://127.0.0.1:8000/api/sync/profiles"
```

#### `POST /api/sync/enqueue`
Encola la sincronización de un perfil existente.

```bash
curl -X POST "http://127.0.0.1:8000/api/sync/enqueue" \
     -H "Content-Type: application/json" \
     -d '{
       "profile_id": "profile_fotos_camara",
       "serial": "192.168.1.100:5555"
     }'
```

**Respuesta (202 Accepted):**
```json
{
  "job_id": "job_f6e5d4c3b2a1",
  "status": "pending",
  "profile_id": "profile_fotos_camara",
  "serial": "192.168.1.100:5555",
  "direction": "phone_to_pc",
  "message": "Sincronización 'Fotos Cámara' encolada exitosamente con ID job_f6e5d4c3b2a1."
}
```

---

### 3.5. Cola de Trabajos (`JobQueueService`)

#### `GET /api/jobs`
Consulta los trabajos con filtros opcionales (`status`, `serial`, `limit`).

```bash
# Listar todos
curl -X GET "http://127.0.0.1:8000/api/jobs"

# Filtrar por estado activo
curl -X GET "http://127.0.0.1:8000/api/jobs?status=running"
```

#### `GET /api/jobs/{job_id}`
Obtiene métricas y estado detallado de un trabajo individual.

```bash
curl -X GET "http://127.0.0.1:8000/api/jobs/job_a1b2c3d4e5f6"
```

#### `POST /api/jobs/{job_id}/pause`
Pausa un trabajo en ejecución conservando su checkpoint de progreso.

```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/job_a1b2c3d4e5f6/pause"
```

#### `POST /api/jobs/{job_id}/resume`
Reanuda un trabajo pausado.

```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/job_a1b2c3d4e5f6/resume"
```

#### `POST /api/jobs/{job_id}/cancel`
Cancela cooperativamente un trabajo activo o encolado.

```bash
curl -X POST "http://127.0.0.1:8000/api/jobs/job_a1b2c3d4e5f6/cancel"
```

---

### 3.6. Explorador de Archivos Remoto

#### `GET /api/explorer/list`
Lista los elementos contenidos en una ruta remota del dispositivo conectado.

```bash
curl -X GET "http://127.0.0.1:8000/api/explorer/list?serial=192.168.1.100:5555&path=/sdcard/Download"
```

**Respuesta (200 OK):**
```json
[
  {
    "path": "/sdcard/Download/documentos",
    "name": "documentos",
    "is_dir": true,
    "size": 0,
    "mtime": "2026-03-15T10:00:00",
    "is_link": false,
    "permissions": "drwxrwx--x",
    "formatted_size": "<DIR>",
    "extension": ""
  },
  {
    "path": "/sdcard/Download/manual.pdf",
    "name": "manual.pdf",
    "is_dir": false,
    "size": 2450123,
    "mtime": "2026-03-15T12:30:00",
    "is_link": false,
    "permissions": "-rw-rw----",
    "formatted_size": "2.34 MB",
    "extension": ".pdf"
  }
]
```

---

## 4. WebSockets de Progreso en Tiempo Real

### 4.1. Canal Global (`/ws/jobs`)
Permite escuchar en tiempo real las actualizaciones de todos los trabajos que se ejecutan o encolan en el sistema.

**Conexión:**
```text
ws://127.0.0.1:8000/ws/jobs
```

**Mensaje inicial al conectar:**
```json
{
  "event": "connected",
  "message": "Suscrito a eventos globales de JobQueueService",
  "active_jobs_count": 2
}
```

**Eventos de actualización en tiempo real:**
```json
{
  "event": "job_update",
  "job_id": "job_a1b2c3d4e5f6",
  "status": "running",
  "progress_percent": 0.45,
  "current_step": "Descargando DCIM/Camera/IMG_001.jpg",
  "bytes_total": 524288000,
  "bytes_completed": 235929600,
  "files_total": 120,
  "files_completed": 54,
  "error": null,
  "job": { ... }
}
```

### 4.2. Canal de Trabajo Específico (`/ws/jobs/{job_id}`)
Suscripción dedicada a un único trabajo. Al conectarse, entrega de inmediato el estado actual del trabajo (`job_init`) y luego transmite cada cambio de progreso (`job_update`).

**Conexión:**
```text
ws://127.0.0.1:8000/ws/jobs/job_a1b2c3d4e5f6
```

**Ejemplo de prueba con `wscat`:**
```bash
npx wscat -c ws://127.0.0.1:8000/ws/jobs
```
