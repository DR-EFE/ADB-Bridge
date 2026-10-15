# UI Automation & Monitor de Notificaciones

Documentación para instalar, configurar y ejecutar el sistema de automatización de interfaz y monitoreo de notificaciones en **cualquier computadora Windows**, respetando la filosofía del proyecto: **uso mínimo de USB, todo el resto por ADB inalámbrico (Wireless Debugging / TCP-IP)**.

---

## 1. ¿Qué es este módulo?

Extiende el proyecto Android Tools (PowerShell + ADB) con una capa de automatización de interfaz usando **uiautomator2** junto a clasificación inteligente con **IA local (Ollama)** y fallback a reglas deterministas.

**Capacidad principal actual:**
- Monitoreo en tiempo real de notificaciones del teléfono.
- Detección y extracción automática de códigos OTP y alertas críticas (bancos, accesos, verificación).
- Clasificación con IA local (Ollama) gestionada de forma automática (se enciende y apaga con el monitor).
- Persistencia estructurada en JSON local (`logs/`) y sincronización opcional en `/sdcard/Download/notificaciones_otp.json`.
- Integración nativa con `devices.json` y el menú de PowerShell (`AndroidTools.ps1`).

---

## 2. Filosofía de conexión: USB Mínimo ➔ Todo por WiFi

En este proyecto, el cable USB solo se usa el tiempo estrictamente necesario (o nunca, si se usa Wireless Debugging por código de vinculación mDNS):

1. **Paso 1 (USB mínimo o Vinculación WiFi):** Conectar el teléfono por USB solo para autorizar la clave RSA y habilitar TCP/IP, o vincular directamente por Wireless Debugging.
2. **Paso 2 (Desconexión de USB):** Desconectar el cable USB inmediatamente después de obtener la IP y puerto ADB.
3. **Paso 3 (Init y Automatización 100% por WiFi):**
   - **`uiautomator2` NO requiere USB para su inicialización (`init`).**
   - El método `ensure_uiautomator_ready()` detecta automáticamente si el dispositivo inalámbrico necesita dependencias y las transfiere por WiFi en segundo plano.
   - Todo el monitoreo, cierre de popups y lectura de pantalla opera a través de la red local.

---

## 3. Requisitos

### Obligatorios
- Windows 10/11 (PowerShell 5.1 o superior).
- Python 3.8 o superior.
- Dispositivo Android en la misma red WiFi, en estado `device` en ADB (ej: `192.168.1.50:5555`).

### Opcionales (para IA Local)
- [Ollama](https://ollama.com/) instalado en Windows.
- Modelo descargado: `ollama pull llama3.2:1b`.
  *(El monitor detecta Ollama, lo inicia en segundo plano si está apagado y lo cierra limpiamente al salir).*

---

## 4. Flujo recomendado de primer uso (Teléfono Nuevo)

1. **Configurar el teléfono:**
   - Activar **Opciones de desarrollador**.
   - Activar **Depuración USB** o **Depuración inalámbrica**.
2. **Conectar mediante el menú de Android Tools:**
   ```powershell
   .\Conectar_Android.cmd
   # o bien: powershell -File .\android_reconnect.ps1
   ```
   - Elige el modo **Wireless Debugging** (Android 11+) o **TCP/IP 5555**.
   - Una vez que figure conectado como `192.168.x.x:puerto`, **desconecta el cable USB**.
3. **Lanzar el Monitor:**
   - No necesitas ejecutar `python -m uiautomator2 init` manualmente. El sistema verifica e inicializa el dispositivo por WiFi en su primera ejecución.

---

## 5. Formas de Ejecución

### Opción A: Desde el Menú Principal Integrado (Recomendado)
Abre el menú interactivo:
```powershell
.\Lanzar_GUI.cmd   # Interfaz gráfica
# o consola tradicional:
powershell -File .\AndroidTools.ps1
```
Selecciona la opción:
`8. Monitor de Notificaciones (UI Automation + IA)`

### Opción B: Script directo de PowerShell (`android_notification_monitor.ps1`)
Usa automáticamente el dispositivo activo guardado en `devices.json` o detectado por ADB:
```powershell
powershell -File .\android_notification_monitor.ps1
```

Parámetros opcionales:
```powershell
# Sin IA (solo reglas deterministas):
powershell -File .\android_notification_monitor.ps1 -NoAi

# Intervalo personalizado y dispositivo específico:
powershell -File .\android_notification_monitor.ps1 -Serial "192.168.1.50:5555" -Interval 2.0

# Sin copiar archivo al teléfono:
powershell -File .\android_notification_monitor.ps1 -NoDeviceCopy
```

### Opción C: Ejecución directa en Python (CLI)
```powershell
python monitor_notificaciones.py --help
```
| Opción | Descripción | Valor por defecto |
|---|---|---|
| `--serial` | Endpoint del teléfono (USB o `IP:PORT`) | Auto-detecta dispositivo activo |
| `--interval` | Segundos entre cada sondeo | `3.0` |
| `--no-ai` | Desactiva Ollama y usa solo reglas | IA activa con fallback a reglas |
| `--no-watchers` | Desactiva auto-clic en diálogos/permisos | Watchers activos |
| `--no-device-copy` | No sube copia a `/sdcard/Download/` | Sí copia |
| `--device-copy-path` | Ruta de la copia en el teléfono | `/sdcard/Download/notificaciones_otp.json` |
| `--logs-dir` | Carpeta local para los registros | `logs` |

---

## 6. Gestión Automática de Ollama (IA Local)

El monitor incorpora `OllamaServiceManager` con el siguiente comportamiento:
- **Al iniciar:**
  - Si `use_ai` está activo (por defecto), comprueba `http://127.0.0.1:11434`.
  - Si Ollama **ya estaba corriendo**, lo usa sin alterar el proceso.
  - Si Ollama **NO estaba corriendo**, busca el ejecutable local y lo arranca automáticamente en segundo plano (`ollama serve`).
- **Al salir (Ctrl+C o finalización):**
  - Si el monitor arrancó Ollama, **lo detiene de forma limpia**.
  - Si Ollama ya estaba abierto antes de iniciar el monitor, **lo deja corriendo**.
  - Si Ollama no está instalado o tarda en responder, cae transparentemente al clasificador de reglas clásicas sin interrumpir el monitoreo.

---

## 7. Inicialización Automática de uiautomator2 (`ensure_uiautomator_ready`)

En `ui_automation/device.py`, el método `get_device()` invoca `ensure_uiautomator_ready(serial)`:
1. Comprueba si el servidor `uiautomator2` ya responde llamadas RPC.
2. Si es la primera vez que se usa en ese dispositivo o tras un reinicio, transfiere los recursos necesarios (`u2.jar`) a través de la conexión ADB activa (funciona exactamente igual por WiFi que por USB).
3. Espera la confirmación de disponibilidad antes de entregar el objeto del dispositivo.
4. **Beneficio:** Cero configuración manual por terminal para el usuario final.

---

## 8. Estructura del Módulo

```text
Android/
├── ui_automation/
│   ├── __init__.py
│   ├── device.py                  # DeviceManager + ensure_uiautomator_ready()
│   ├── ollama.py                  # OllamaServiceManager (arranque y apagado automático)
│   ├── watchers.py                # UIWatcherManager + setup_auto_popup_watcher()
│   ├── notifications.py           # Extracción dumpsys/UI + clasificación (IA y reglas)
│   └── monitor.py                 # NotificationMonitor (orquestador resiliente)
├── android_notification_monitor.ps1 # Integración nativa con PowerShell y devices.json
├── monitor_notificaciones.py      # Entrypoint CLI Python
├── logs/                          # Registros locales JSON
│   ├── notificaciones_importantes.json
│   └── notifications_YYYY-MM-DD.json
└── UI_AUTOMATION.md               # Este documento
```

---

## 9. Solución de Problemas

| Problema | Causa | Solución |
|---|---|---|
| `Dispositivo desconectado` o `offline` | La conexión inalámbrica caducó o cambió de IP | Ejecuta `android_reconnect.ps1` o la opción 1 del menú para reasociar la IP WiFi. |
| `No se detectaron dispositivos ADB` | El teléfono no está en la red o perdió depuración | Comprueba que el teléfono esté en la misma WiFi y con **Depuración inalámbrica** activa. |
| `Ollama no responde` | Modelo no descargado o falta de RAM | El sistema pasa automáticamente a reglas clásicas. Para activar IA: ejecuta `ollama pull llama3.2:1b`. |
| Diálogos del sistema bloquean la pantalla | Permisos o alertas pendientes | Los watchers de `watchers.py` hacen clic automático en "Permitir", "Aceptar", "Continuar", "OK". |
