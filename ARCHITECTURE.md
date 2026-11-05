# Arquitectura final de Android Tools

## Estructura objetivo

```text
Android/
├── AndroidAdb.psm1                  # Núcleo: ADB, selección, configuración, seguridad
├── config/
│   ├── devices.example.json         # Plantilla del registro multi-dispositivo
│   └── android_config.example.json  # Plantilla antigua, solo compatibilidad
├── setup.ps1                        # Punto de entrada público: Setup
├── android_setup_v2.ps1             # Implementación de Setup moderno/legado
├── android_reconnect.ps1            # Punto de entrada: Reconnect
├── android_transfer.ps1             # Punto de entrada: Transfer
├── android_file_explorer.ps1        # Punto de entrada: Explorer
├── android_advanced_backup.ps1      # Punto de entrada: Backup
├── AutorizacionWeb.ps1              # Recuperación explícita de autorización
├── SETUP.bat                        # Lanzador de setup.ps1
├── RECONNECT.bat                    # Lanzador de android_reconnect.ps1
└── TRANSFER.bat                     # Lanzador de android_transfer.ps1
```

El registro activo se guarda fuera del repositorio en `%USERPROFILE%\AndroidTools\devices.json`. Puede contener varios teléfonos y tablets. Si aún existe el antiguo `%USERPROFILE%\AndroidTools\android_config.json`, el módulo lo migra automáticamente al crear el registro nuevo; Setup también lo mantiene por compatibilidad con herramientas antiguas.

## Contratos del módulo (Fase 1: PowerShell + Python)

### PowerShell: AndroidAdb.psm1
- `Invoke-Adb -Serial ...`: única vía para comandos dirigidos a un dispositivo. Siempre usa `adb -s <serial>`, soporta `-TimeoutSeconds`, `-Retries`, `-RetryDelaySeconds`, y falla si ADB devuelve un código distinto de cero cancelando el proceso si excede el tiempo límite.
- `Invoke-AdbHost`: única vía para comandos del host sin serial: `pair`, `connect`, `disconnect` y `mdns services`, con soporte idéntico de timeouts y reintentos.
- `Get-SingleReadyDevice [-Serial <serial>]`: si no se pasa serial, resuelve el dispositivo único listo (`device`). Falla con `DISPOSITIVO_NO_SELECCIONADO` si no hay ninguno, o con `MULTIPLE_DEVICES` si hay >1 (prohibiendo la selección silenciosa arbitraria). Si se pasa serial, valida que esté en estado `device` o falla con `DISPOSITIVO_NO_LISTO`.
- `Get-CurrentAdbDevice`, `Set-CurrentAdbDevice`, `Clear-CurrentAdbDevice` y `Resolve-AdbSerial`: selección global durante la sesión de PowerShell.
- `Disconnect-AdbEndpoint` y `Disconnect-AdbDevice`: desconexión controlada de endpoints TCP/IP; para dispositivos USB físicos, arroja la excepción controlada `USB_DISCONNECT_UNSUPPORTED` indicando desconexión manual del cable.
- Funciones unificadas de alto nivel (todas con serial explícito o validación `Get-SingleReadyDevice`):
  - `Invoke-AdbShell -Command <str> [-Serial <str>] [-TimeoutSeconds <int>] [-Retries <int>]`
  - `Invoke-AdbPull -RemotePath <str> -LocalPath <str> [-Serial <str>] [-TimeoutSeconds <int>]`
  - `Invoke-AdbPush -LocalPath <str> -RemotePath <str> [-Serial <str>] [-TimeoutSeconds <int>]`
  - `Install-AdbPackage -ApkPath <str> [-Serial <str>] [-Options <str[]>] [-TimeoutSeconds <int>]`
  - `Add-AdbForward -LocalSpec <str> -RemoteSpec <str> [-Serial <str>]`
  - `Pair-AdbDevice -Endpoint <str> -PairingCode <str> [-TimeoutSeconds <int>]`
  - `Get-AdbProperty -PropertyName <str> [-Serial <str>]`
- Logging estructurado (`Write-AdbLog`): formato con timestamp ISO 8601 UTC, nivel, origen, serial opcional (`[Serial=...]`), comando opcional (`[Cmd=...]`), código de salida (`[ExitCode=...]`), sanitización y truncamiento defensivo contra salidas masivas.

### Python: Arquitectura por Capas (core/ y docs/ARQUITECTURA_POR_CAPAS.md)
- El código en Python está estructurado según la arquitectura limpia por capas:
  - **Dominio (core/domain/)**: Modelos pydantic tipados (models/adb.py, models/jobs.py, models/backup.py, etc.) y reglas puras (
ules/).
  - **Infraestructura (core/infrastructure/)**: Adaptadores para db_service.py, persistencia database/sqlite.py, red 
etwork_scan.py e IA ollama_client.py.
  - **Servicios de Aplicación (core/services/)**: Casos de uso (device_service.py, job_queue_service.py, ackup_service.py, sync_service.py, etc.).
  - **Presentación / API (ackend/, ui/)**: Desacopladas mediante inyección de dependencias desde core.*.
  - **Fachada retrocompatible (ui/services/)**: Mantiene alias transparentes en sys.modules para compatibilidad 100% retroactiva.
- Mismo contrato y semántica que PowerShell:
  - `AdbService.devices() -> List[Device]`
  - `AdbService.resolve_serial(serial=None) -> str`: misma política estricta de no asumir dispositivo cuando hay >1.
  - `AdbService.connect(endpoint, timeout) -> ConnectionAttempt`: no guarda endpoints si la conexión o autorización falló.
  - `AdbService.disconnect(endpoint_or_serial) -> OperationResult`: lanza `AdbError(USB_DISCONNECT_UNSUPPORTED)` si es USB físico.
  - `AdbService.shell(cmd, serial, timeout, retries) -> OperationResult`
  - `AdbService.pull(remote, local, serial, timeout, retries) -> OperationResult`
  - `AdbService.push(local, remote, serial, timeout, retries) -> OperationResult`
  - `AdbService.install(apk, serial, options, timeout, retries) -> OperationResult`
  - `AdbService.forward(local_spec, remote_spec, serial, timeout) -> OperationResult`
  - `AdbService.pair(endpoint, code, timeout) -> ConnectionAttempt`: valida 6 dígitos y reporta `ConnectionAttempt`.
  - `AdbService.get_property(prop, serial, timeout) -> str`
- Modelos Pydantic v2:
  - `Device(serial, state, is_network, friendly_name, model, battery_level, android_version, usb_serial, physical_id_hash)`
  - `ConnectionAttempt(endpoint, host, port, mode, success, message, exit_code, timestamp_utc, physical_id_hash)`
  - `OperationResult(success, exit_code, stdout, stderr, command, serial, duration_seconds, message)`

## Flujos de conexión

### Wireless Debugging TLS

1. `adb pair HOST:PUERTO_PAIRING CODIGO` mediante `Invoke-AdbHost`.
2. Resolver `adb-tls-connect` mediante mDNS o pedir el puerto de conexión.
3. `Connect-AdbEndpoint HOST:PUERTO_CONEXION`.
4. Esperar el estado `device` y guardar `Mode = WirelessTls`.

El puerto de conexión TLS no se asume como `5555`.

### TCP/IP USB legado

1. Obtener exactamente un dispositivo USB en estado `device`.
2. Ejecutar `Invoke-Adb -Serial USB_SERIAL -Arguments @('tcpip','5555')`.
3. Detectar o pedir la IP y conectar `HOST:5555`.
4. Esperar el estado `device` y guardar `Mode = LegacyTcpIp`.

`5555` solo pertenece a este flujo legado.

## Responsabilidades de puntos de entrada

| Script | Responsabilidad |
|---|---|
| `setup.ps1` | Entrada pública y delegación al setup implementado. |
| `android_setup_v2.ps1` | Pairing TLS y activación TCP/IP USB legado; agrega o actualiza el dispositivo en `devices.json` solo después de `device`. |
| `android_reconnect.ps1` | Administra dispositivos guardados y ejecuta reconexión inteligente: último endpoint, mDNS/USB y captura manual. |
| `android_transfer.ps1` | Operaciones pull/push y backup de medios; recibe y valida resultados de jobs. |
| `android_file_explorer.ps1` | Navegación y búsqueda segura por rutas validadas. |
| `android_advanced_backup.ps1` | Respaldo de datos disponibles y manifiesto por categoría. |
| `AutorizacionWeb.ps1` | Recuperación explícita, incluida la eventual reinicialización de ADB; no forma parte del flujo normal. |

## Política de errores y seguridad

- Todo error ADB debe detener la operación concreta y conservar su salida/código.
- Un respaldo solo es completo si todas sus categorías informan `OK`.
- Entradas para shell y nombres de carpeta se validan a través de las funciones del módulo.
- Las carpetas de backup aplican ACL restringida; se recomienda cifrado de disco o EFS para datos sensibles.
- Los lanzadores usan `-NoProfile -ExecutionPolicy RemoteSigned`, nunca `Bypass`.

## Reconexión inteligente de la GUI

`DeviceService` mantiene un historial acotado de endpoints en cada dispositivo y expone `discover_and_reconnect_wireless`, `list_known_endpoints`, `save_successful_endpoint` y `run_network_diagnostics`. `start_connection_watchdog` ejecuta reintentos con retroceso (2 s a 30 s) durante un máximo de 120 s; se detiene al cambiar de dispositivo o cerrar la GUI.

Los endpoints mDNS se prefieren cuando están disponibles. Si mDNS no cruza una VPN, se prueban los endpoints históricos. La aplicación detecta interfaces Tailscale/WireGuard para diagnóstico, pero no instala ni configura VPN y no abre ADB a Internet.
