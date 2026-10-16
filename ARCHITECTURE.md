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

## Contratos del módulo

- `Invoke-Adb -Serial ...`: única vía para comandos dirigidos a un dispositivo. Siempre usa `adb -s` y falla si ADB devuelve un código distinto de cero.
- `Get-CurrentAdbDevice`, `Set-CurrentAdbDevice`, `Clear-CurrentAdbDevice` y `Resolve-AdbSerial`: selección global durante la sesión de PowerShell.
- `Test-AdbDeviceReady`: verifica `get-state=device`, registra el resultado y puede intentar la reconexión automática.
- `Invoke-AdbCommand`: wrapper obligatorio para comandos de trabajo; resuelve el dispositivo seleccionado y siempre ejecuta con `adb -s`.
- `Select-CurrentAdbDevice`: muestra los dispositivos guardados y obliga a elegir uno en estado exacto `device`.
- `Invoke-AdbHost`: única vía para comandos sin serial: `pair`, `connect` y `mdns services`.
- `Get-SingleReadyDevice`: falla ante cero o más de un dispositivo `device`; no selecciona silenciosamente.
- `Wait-AdbDevice`: considera éxito solo el estado exacto `device`.
- `Read-AdbDeviceStore` y `Save-AdbDeviceStore`: lectura validada y escritura segura del registro multi-dispositivo.
- `Get-SavedAdbDevices`, `Add-SavedAdbDevice`, `Update-SavedAdbDevice` y `Remove-SavedAdbDevice`: operaciones CRUD sobre `devices.json`.
- `Get-AdbMdnsServices`: descubre endpoints `adb-tls-connect` antes de solicitar IP y puerto al usuario.

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
