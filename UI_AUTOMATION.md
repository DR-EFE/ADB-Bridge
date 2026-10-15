# UI Automation & Monitor de Notificaciones

Documentación para poder instalar, configurar y ejecutar el sistema de monitoreo de notificaciones en **cualquier computadora Windows**.

---

## 1. ¿Qué es este módulo?

Extiende el proyecto Android Tools (PowerShell + ADB) con una capa de automatización de interfaz usando **uiautomator2** + clasificación opcional con **IA local (Ollama)**.

**Capacidad principal actual:**
- Monitoreo en tiempo real de notificaciones del teléfono
- Detección automática de OTP y alertas importantes
- Guardado local en el PC + copia opcional en el teléfono
- Clasificación inteligente (IA local) con fallback a reglas clásicas

---

## 2. Requisitos

### Obligatorios
- Windows 10/11
- Python 3.8 o superior
- ADB (Android Platform Tools) instalado y en el `PATH`
- Teléfono Android con **Depuración USB** o **Depuración inalámbrica** activada
- El teléfono debe aparecer como `device` en `adb devices`

### Opcionales (recomendados)
- [Ollama](https://ollama.com/) instalado (para clasificación con IA local)
- Modelo ligero: `llama3.2:1b` (o cualquier otro modelo de texto disponible)

---

## 3. Instalación (paso a paso)

Abre PowerShell o CMD en la carpeta del proyecto (`Android/`).

### 3.1 Verificar Python
```powershell
python --version
```
Si no está instalado o es menor a 3.8, instálalo desde [python.org](https://www.python.org/downloads/) o Microsoft Store.

### 3.2 Instalar dependencias Python
```powershell
pip install uiautomator2
```

### 3.3 Inicializar uiautomator2 en el teléfono
Con el teléfono conectado y autorizado:
```powershell
python -m uiautomator2 init
```
Este comando instala el servicio necesario en el dispositivo (solo se hace una vez por teléfono).

### 3.4 (Opcional) Preparar IA local
```powershell
# Instalar Ollama desde https://ollama.com
ollama pull llama3.2:1b
```
Deja Ollama corriendo en segundo plano.

---

## 4. Estructura de archivos

```text
Android/
├── ui_automation/
│   ├── __init__.py
│   ├── device.py              # Gestión de conexión al dispositivo
│   ├── watchers.py            # Watchers automáticos de popups
│   ├── notifications.py       # Extracción + clasificación (reglas + IA)
│   └── monitor.py             # Motor de monitoreo en tiempo real
├── monitor_notificaciones.py  # Punto de entrada principal (CLI)
├── logs/                      # Aquí se guardan los JSON de eventos
└── UI_AUTOMATION.md           # Esta documentación
```

---

## 5. Cómo ejecutar el monitor

### Uso básico
```powershell
python monitor_notificaciones.py
```

### Opciones disponibles
```powershell
python monitor_notificaciones.py --help
```

| Opción | Descripción | Valor por defecto |
|--------|-------------|-------------------|
| `--serial` | Serial del dispositivo ADB | Auto-detecta |
| `--interval` | Segundos entre cada revisión | 4 |
| `--no-ai` | Desactiva la clasificación con IA | IA activada |
| `--no-device-copy` | No sube copia al teléfono | Sí sube |
| `--device-copy-path` | Ruta en el teléfono para la copia | `/sdcard/Download/notificaciones_otp.json` |
| `--log-dir` | Carpeta de logs en el PC | `./logs` |

### Ejemplos
```powershell
# Uso normal (con IA si Ollama está disponible)
python monitor_notificaciones.py

# Forzar solo reglas (sin IA)
python monitor_notificaciones.py --no-ai

# Intervalo más rápido + dispositivo específico
python monitor_notificaciones.py --serial 192.168.1.50:5555 --interval 3

# Sin guardar copia en el teléfono (más privado)
python monitor_notificaciones.py --no-device-copy
```

---

## 6. Qué hace el monitor

1. Se conecta al teléfono mediante uiautomator2.
2. Activa un Watcher que acepta automáticamente popups comunes ("Permitir", "Aceptar", etc.).
3. Cada X segundos:
   - Lee las notificaciones (prioriza `dumpsys` para no abrir la cortina innecesariamente).
   - Detecta solo las **nuevas**.
   - Clasifica cada una (IA local → fallback a reglas).
   - Si es **OTP** o **ALERTA**:
     - Extrae el código si existe.
     - Guarda el evento en `logs/notificaciones_importantes.json`.
     - Sube una copia al teléfono (opcional).
     - Muestra un resumen claro en consola.
4. Se detiene limpiamente con `Ctrl+C`.

---

## 7. Archivos generados

| Archivo | Ubicación | Contenido |
|---------|-----------|-----------|
| `notificaciones_importantes.json` | `logs/` (PC) | Historial de OTP y alertas detectadas |
| `notifications_YYYY-MM-DD.json` | `logs/` (PC) | Todas las notificaciones del día (opcional) |
| `notificaciones_otp.json` | `/sdcard/Download/` (teléfono) | Copia de los eventos importantes |

---

## 8. Integración con el sistema PowerShell existente

El monitor es independiente, pero se puede lanzar fácilmente desde tus scripts actuales.

Ejemplo de wrapper simple (`Invoke-NotificationMonitor.ps1`):

```powershell
param(
    [string]$Serial,
    [int]$Interval = 4,
    [switch]$NoAi,
    [switch]$NoDeviceCopy
)

# Verificar que el dispositivo esté listo (usando tu módulo)
Import-Module .\AndroidAdb.psm1 -Force
if ($Serial) {
    Set-CurrentAdbDevice -Serial $Serial
}
Test-AdbDeviceReady -ErrorAction Stop

$argsList = @()
if ($Serial)        { $argsList += "--serial", $Serial }
if ($Interval)      { $argsList += "--interval", $Interval }
if ($NoAi)          { $argsList += "--no-ai" }
if ($NoDeviceCopy)  { $argsList += "--no-device-copy" }

python monitor_notificaciones.py @argsList
```

---

## 9. Solución de problemas comunes

| Problema | Solución |
|----------|----------|
| `python` no se reconoce | Añade Python al PATH o usa `py` |
| `uiautomator2` no conecta | Ejecuta `python -m uiautomator2 init` y acepta permisos en el teléfono |
| Dispositivo `unauthorized` | Revisa la autorización RSA en el teléfono |
| Ollama no responde | El sistema usa automáticamente las reglas clásicas (no se rompe) |
| No detecta OTP | Prueba con `--no-ai` para ver solo reglas, o revisa el texto exacto de la notificación |
| Error de permisos al escribir en logs | Ejecuta PowerShell como administrador o cambia `--log-dir` |

---

## 10. Seguridad

- Los códigos OTP se guardan en **texto plano**.
- La copia en `/sdcard/Download/` puede ser leída por otras apps.
- Recomendación: usa `--no-device-copy` si el teléfono es compartido o no confías en todas las apps instaladas.
- Los logs del PC se recomienda mantenerlos en una carpeta con permisos restringidos.

---

## 11. Próximas mejoras posibles

- Wrapper PowerShell completo integrado en el menú principal
- Soporte de JSON Lines
- Filtro para guardar solo OTP (no ALERTAS) en el teléfono
- Notificaciones de Windows cuando llegue un OTP
- Soporte multi-dispositivo simultáneo

---

**Documento creado para permitir la reproducción completa del sistema en cualquier PC.**
