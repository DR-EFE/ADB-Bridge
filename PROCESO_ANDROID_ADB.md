# Proceso operativo de conexión Android mediante ADB

Guía de preparación, conexión, reconexión y uso posterior del proyecto.

## 1. Preparación

Instala Android Platform Tools y verifica ADB:

```powershell
adb version
```

Abre PowerShell en la carpeta del proyecto:

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
```

Los lanzadores utilizan `RemoteSigned`; no uses `ExecutionPolicy Bypass`.

El logging se guarda en `%USERPROFILE%\AndroidTools\logs\adb-tools-YYYY-MM-DD.log`. Para activar DEBUG antes de ejecutar un script:

```powershell
Import-Module .\AndroidAdb.psm1 -Force
Initialize-AdbLog -EnableDebug
Get-AdbLogPath
```

Para desactivarlo:

```powershell
Initialize-AdbLog -DisableDebug
```

Los niveles disponibles son `INFO`, `WARN`, `ERROR`, `DEBUG` y `SUCCESS`. Se conservan automáticamente los logs de los últimos 15 días.

## 2. Wireless Debugging moderno

En el teléfono activa **Depuración inalámbrica**, conecta ambos equipos a la misma Wi-Fi y abre **Vincular dispositivo con código de emparejamiento**.

Ejecuta:

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
& .\setup.ps1
```

Selecciona `1` e introduce el nombre amigable, IP, puerto de pairing y código de seis dígitos.

El pairing corresponde a:

```powershell
adb pair HOST:PUERTO_PAIRING CODIGO
```

Después el asistente busca el servicio `_adb-tls-connect` mediante mDNS. El puerto TLS de conexión puede ser distinto del puerto de pairing y no debe reemplazarse por `5555`. Si mDNS falla, introduce manualmente la IP y el puerto de conexión mostrados por el teléfono.

Valida la conexión:

```powershell
adb devices -l
```

El estado válido es exactamente `device`; `offline` y `unauthorized` no son válidos.

## 3. Método USB/TCP-IP legado

Activa **Depuración USB**, conecta el teléfono por cable, acepta la autorización RSA y confirma que ambos equipos estén en la misma Wi-Fi.

Ejecuta:

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
& .\setup.ps1
```

Selecciona `2`. El flujo utiliza el serial USB seleccionado y ejecuta conceptualmente:

```powershell
adb devices
adb -s SERIAL_USB shell ip route
adb -s SERIAL_USB tcpip 5555
adb connect IP_DEL_TELEFONO:5555
adb devices
```

El puerto `5555` solo pertenece a este método legado.

## 4. Registro persistente

Después de una conexión correcta se actualiza:

```text
%USERPROFILE%\AndroidTools\devices.json
```

El registro puede contener varios dispositivos y guarda `Id`, `FriendlyName`, `UsbSerial`, `LastEndpoint`, `Mode`, `LastConnectedUtc` e `IsPaired`. La selección activa se conserva en memoria durante la sesión de PowerShell y, al usar `-Persist`, también se registra como `CurrentDeviceId`.

Administra el registro con:

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
& .\android_reconnect.ps1
```

El menú permite reconectar, agregar, renombrar, eliminar y consultar endpoints. El antiguo `android_config.json` se migra automáticamente si todavía existe y aún no se creó `devices.json`.

## 5. Reconexión tras cambiar de red

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
& .\android_reconnect.ps1
```

El flujo intenta, en orden:

1. Último `LastEndpoint` conocido.
2. Nuevo endpoint TLS mediante mDNS.
3. Para TCP/IP legado, detección de IP por USB y reactivación de `5555`.
4. IP y puerto introducidos manualmente.

El JSON solo se actualiza después de confirmar estado `device`. Los scripts de transferencia y respaldo muestran los dispositivos guardados si no existe una selección activa y no continúan hasta elegir uno conectado en estado `device`. También puede usarse:

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
& .\RECONNECT.bat
```

## 6. Validación manual

Primero define el serial en la sesión actual de PowerShell. La variable no se conserva automáticamente entre ventanas o sesiones:

```powershell
adb devices -l
$serial = '192.168.1.42:37123'
adb -s $serial get-state
adb -s $serial shell getprop ro.product.model
```

`get-state` debe devolver `device`. Cuando existan varios dispositivos, siempre identifica explícitamente el serial correcto.

## 7. Comandos después de conectar

Usa `-s $serial` en todos los comandos dirigidos al teléfono. Si `$serial` está vacío, ADB interpretará incorrectamente los argumentos.

Para la conexión actual usa:

```powershell
$serial = '192.168.0.37:5555'
adb -s $serial get-state
```

### Información y almacenamiento

```powershell
adb -s $serial shell getprop ro.product.manufacturer
adb -s $serial shell getprop ro.product.model
adb -s $serial shell getprop ro.build.version.release
adb -s $serial shell df -h /sdcard
```

### Listar archivos

```powershell
adb -s $serial shell find /sdcard/Download -maxdepth 1 -type f -print
adb -s $serial shell find /sdcard/DCIM -maxdepth 2 -type f -print
```

### Descargar un directorio

```powershell
$destination = Join-Path $PWD 'AndroidDownload'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
adb -s $serial pull /sdcard/Download $destination
if ($LASTEXITCODE -ne 0) { throw 'La descarga fallo.' }
```

### Subir un archivo

```powershell
$localFile = Join-Path $PWD 'ejemplo.txt'
adb -s $serial push $localFile /sdcard/Download/
if ($LASTEXITCODE -ne 0) { throw 'La subida fallo.' }
```

### Consulta remota

```powershell
adb -s $serial shell getprop ro.serialno
if ($LASTEXITCODE -ne 0) { throw 'El comando remoto fallo.' }
```

No concatentes texto del usuario dentro de comandos `adb shell`. Para las operaciones del proyecto usa `AndroidAdb.psm1` e `Invoke-Adb`.

## 8. Scripts disponibles

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
& .\android_transfer.ps1
& .\android_file_explorer.ps1
& .\android_list_downloadable.ps1
& .\android_advanced_backup.ps1
```

Los respaldos pueden incluir contactos, SMS, llamadas, configuraciones y otros datos personales. Protege el directorio de backups y considera cifrado.

## 9. Errores frecuentes

### `unauthorized`

Desbloquea el teléfono y acepta el diálogo RSA. Comprueba:

```powershell
adb devices
```

Si persiste, conecta por USB y repite `setup.ps1`. `AutorizacionWeb.ps1` queda como recuperación explícita.

### `offline`

No ejecutes transferencias. Reconecta el endpoint:

```powershell
adb disconnect HOST:PUERTO
adb connect HOST:PUERTO
adb devices
```

Continúa solo cuando aparezca `device`.

### No aparece mDNS

Confirma misma red Wi-Fi, Wireless Debugging activo y ausencia de aislamiento entre clientes. Como alternativa, introduce manualmente el puerto TLS de conexión.

### Cambió la IP

Ejecuta `android_reconnect.ps1`. En TLS también puede cambiar el puerto, por lo que no basta con sustituir la IP.

### Error `10013` o puerto `5555` no alcanzable

Comprueba desde Windows:

```powershell
Test-NetConnection -ComputerName 192.168.0.37 -Port 5555 -InformationLevel Detailed
```

El resultado `TcpTestSucceeded : True` es necesario antes de que `adb connect` pueda funcionar. Si aparece `False`:

- Confirma que la IP del teléfono siga siendo la correcta.
- Conecta PC y teléfono a la misma red, no a una red de invitados.
- Desactiva temporalmente VPN, aislamiento de clientes Wi-Fi o filtros de seguridad para probar.
- Permite `adb.exe` en la red privada del Firewall de Windows.
- Desconecta el cable USB después de ejecutar `adb tcpip 5555` y vuelve a probar.
- Ejecuta otra vez `adb devices` para confirmar que el teléfono sigue autorizado por USB.

Prueba manualmente:

```powershell
adb disconnect 192.168.0.37:5555
adb connect 192.168.0.37:5555
adb devices -l
```

Si `Test-NetConnection` continúa en `False`, el problema está fuera del registro JSON: el teléfono no es alcanzable por TCP desde esa red o una política de seguridad bloquea la conexión.

### Varios dispositivos

```powershell
adb devices -l
$serial = 'SERIAL_EXACTO'
adb -s $serial get-state
```

### `adb kill-server`

No lo uses durante el flujo normal: desconecta todos los dispositivos y otros procesos ADB. Déjalo solo para recuperación explícita.

## 10. Checklist antes de transferir o respaldar

- El dispositivo correcto aparece en `adb devices -l`.
- El estado es exactamente `device`.
- Usas el serial correcto con `-s` cuando hay varios dispositivos.
- La PC y el teléfono siguen en la misma red inalámbrica.
- `devices.json` se actualizó después de conectar.
- Las transferencias validan `$LASTEXITCODE`.
- El directorio de backups está protegido.
