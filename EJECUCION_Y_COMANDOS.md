# Android ADB Tools — ejecución, comandos y errores

Guía operativa del estado actual del proyecto. Esta carpeta contiene scripts para administrar un teléfono Android desde Windows mediante ADB; no es una aplicación Android para instalar en el teléfono.

## 1. Requisitos

- Windows PowerShell 5.1.
- Android Platform-Tools instalado y `adb` disponible en `PATH`.
- Depuración USB o Depuración inalámbrica habilitada.
- Autorización RSA aceptada en el teléfono.
- Para conexión inalámbrica: PC y teléfono en la misma red.
- `scrcpy` solo es necesario para la opción de control/pantalla en vivo.

Comprobación inicial:

```powershell
adb version
adb devices -l
Get-Command adb
```

El estado correcto del dispositivo es exactamente `device`. Los estados `offline` y `unauthorized` no permiten continuar con operaciones confiables.

## 2. Inicio rápido

Abrir PowerShell en el proyecto:

```powershell
Set-Location -LiteralPath 'C:\Users\pipe6\Downloads\Android'
```

Configurar el teléfono por primera vez:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\setup.ps1
```

Después de conectar:

```powershell
adb devices -l
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\AndroidTools.ps1
```

También existen estos lanzadores:

| Archivo | Uso |
|---|---|
| `SETUP.bat` | Configuración inicial USB o Wi-Fi. |
| `RECONNECT.bat` | Reconectar y administrar dispositivos guardados. |
| `TRANSFER.bat` | Transferir archivos y ejecutar backup de medios. |
| `Conectar_Android.cmd` | Alias de `setup.ps1`. |
| `Lanzar_GUI.cmd` | Abrir la interfaz gráfica. |
| `Lanzar_GUI.bat` | Abrir la interfaz gráfica con doble clic. |
| `Ejecutar_Pruebas.cmd` | Ejecutar pruebas. |

Los lanzadores principales usan `RemoteSigned`. Actualmente `Lanzar_GUI.cmd` y `Ejecutar_Pruebas.cmd` todavía usan `ExecutionPolicy Bypass`; si la política de seguridad lo prohíbe, ejecútalos manualmente con `RemoteSigned` como se indica más adelante.

## 3. Configuración de conexión

### Wireless Debugging moderno

En el teléfono activa **Opciones de desarrollador → Depuración inalámbrica → Vincular dispositivo con código de emparejamiento** y ejecuta `setup.ps1`. Selecciona la opción `1`.

El asistente solicita nombre, host, puerto de pairing y código. El flujo equivale a:

```powershell
adb pair HOST:PUERTO_PAIRING CODIGO
adb mdns services
adb connect HOST:PUERTO_CONEXION
adb devices -l
```

El puerto de pairing y el puerto TLS de conexión son distintos. No se debe forzar `5555` en este flujo.

### USB + TCP/IP legado

Conecta el teléfono por USB, acepta la autorización RSA y selecciona la opción `2` en `setup.ps1`.

El flujo usa:

```powershell
adb devices
adb -s SERIAL_USB shell ip route
adb -s SERIAL_USB tcpip 5555
adb connect IP_DEL_TELEFONO:5555
adb devices -l
```

El puerto `5555` corresponde únicamente al método TCP/IP legado.

### Reconexión

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\android_reconnect.ps1
```

Opciones del menú:

1. Reconectar dispositivo guardado.
2. Agregar dispositivo.
3. Renombrar dispositivo.
4. Eliminar dispositivo — exige escribir `ELIMINAR`.
5. Ver dispositivos y endpoints.
6. Salir.

La reconexión intenta el último endpoint, mDNS, USB/TCP-IP y finalmente datos introducidos manualmente.

## 4. Menú principal

Ejecutar:

```powershell
& .\AndroidTools.ps1
```

Opciones actuales:

| Opción | Script | Función |
|---:|---|---|
| 1 | `android_reconnect.ps1` | Reconexión y registro de dispositivos. |
| 2 | `android_transfer.ps1` | Fotos, documentos, subida, backup de medios, búsqueda y espacio. |
| 3 | `android_file_explorer.ps1` | Explorar, descargar y subir archivos. |
| 4 | `android_advanced_backup.ps1` | Extracciones avanzadas sin root. |
| 5 | `android_apps.ps1` | Listar o desinstalar aplicaciones de usuario. |
| 6 | `android_screen.ps1` | Capturas, grabación y `scrcpy`. |
| 7 | `android_sync.ps1` | Perfiles de sincronización. |
| 9 | `android_diagnostics.ps1` | Diagnóstico de ADB, mDNS, dispositivo, logs y espacio. |
| 0 | — | Salir. |

La opción `8` no está definida actualmente.

## 5. Scripts y opciones útiles

### Transferencias

```powershell
& .\android_transfer.ps1
```

El menú permite:

- Fotos/videos: recientes, DCIM, Movies o ambos.
- Documentos: recientes, `Documents`, PDFs o `Download`.
- Subir un archivo o carpeta a `/sdcard/Upload/`.
- Backup de `DCIM`, `Download`, `Documents`, `Music` y `Pictures`.
- Buscar hasta 20 coincidencias en `/sdcard`.
- Consultar espacio de `/data` y `/sdcard`.

### Explorador de archivos

```powershell
& .\android_file_explorer.ps1
```

Acciones principales: número para entrar en un elemento, `..` o `p` para subir, `d` descargar, `u` subir, `i` información, `s` buscar, `q` accesos rápidos, `h` historial y `x` salir.

Accesos rápidos: `qa1` `/sdcard`, `qa2` `DCIM`, `qa3` `Download`, `qa4` `Documents`, `qa5` `Music`, `qa6` `Movies`, `qa7` `Pictures`, `qa8` `WhatsApp`, `qa9` `Android` y `qa10` `/data`.

### Descarga de carpetas

```powershell
& .\android_list_downloadable.ps1
```

Incluye DCIM, capturas, música, videos, imágenes, descargas, documentos, `Android/media`, `Android/obb`, `Android/data`, multimedia completo, `/sdcard/` completo y una carpeta personalizada.

La descarga total de `/sdcard/` puede tardar horas y requiere espacio local equivalente al contenido del teléfono.

### Backup avanzado

```powershell
& .\android_advanced_backup.ps1
```

Categorías: contactos, SMS, llamadas, calendario, configuraciones, aplicaciones, Wi-Fi, Bluetooth, diccionario y clipboard. La opción `11` ejecuta todas las categorías.

Este backup es una extracción best-effort sin root. Una categoría puede terminar como `OK`, `EMPTY`, `DENIED`, `PARTIAL` o `ERROR`. Solo debe considerarse completo si el manifiesto indica que todas las categorías están en `OK`.

### Pantalla

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\android_screen.ps1
```

Para abrir la GUI directamente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -STA -File .\AndroidTools.GUI.ps1
```

La opción de `scrcpy` requiere que `scrcpy.exe` esté instalado y disponible en `PATH`.

### Diagnóstico

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\android_diagnostics.ps1
```

Revisa ADB, dispositivos, servicios mDNS, `devices.json`, selección actual, últimos errores del log y espacio libre local/remoto.

### Aplicaciones

```powershell
& .\android_apps.ps1
```

La desinstalación exige escribir `DESINSTALAR`. Solo se validan paquetes con formato similar a `com.example.app`; confirmar la operación antes de continuar.

### Sincronización por perfiles

```powershell
& .\android_sync.ps1
```

Permite crear, listar, ejecutar y eliminar perfiles con rutas Android, extensiones, rango de días, tamaño mínimo/máximo y control de sobrescritura.

## 6. Comandos ADB de diagnóstico y trabajo

Siempre que haya más de un dispositivo, usar el serial explícito con `-s`:

```powershell
adb devices -l
$serial = 'SERIAL_O_HOST:PUERTO'   # reemplazar por el valor real
adb -s $serial get-state
adb -s $serial shell getprop ro.product.manufacturer
adb -s $serial shell getprop ro.product.model
adb -s $serial shell getprop ro.build.version.release
adb -s $serial shell df -h /sdcard
```

Listar archivos:

```powershell
adb -s $serial shell find /sdcard/Download -maxdepth 1 -type f -print
adb -s $serial shell find /sdcard/DCIM -maxdepth 2 -type f -print
```

Descargar y subir:

```powershell
$destination = Join-Path $PWD 'AndroidDownload'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
adb -s $serial pull /sdcard/Download $destination

$localFile = Join-Path $PWD 'ejemplo.txt'
adb -s $serial push $localFile /sdcard/Download/
```

Conexión y red:

```powershell
adb pair HOST:PUERTO_PAIRING CODIGO
adb mdns services
adb disconnect HOST:PUERTO
adb connect HOST:PUERTO
Test-NetConnection -ComputerName HOST -Port PUERTO -InformationLevel Detailed
adb server-status
```

No usar `adb kill-server` durante el flujo normal: desconecta los dispositivos y afecta otros procesos que estén usando ADB. Reservarlo para recuperación explícita.

## 7. Logs, configuración y salidas

```powershell
$adbTools = Join-Path $env:USERPROFILE 'AndroidTools'
Get-Content (Join-Path $adbTools 'devices.json')
Get-ChildItem (Join-Path $adbTools 'logs')
Get-ChildItem (Join-Path $adbTools 'backups')
Get-Content (Join-Path $adbTools 'sync-profiles.json')
```

Ubicaciones principales:

- Registro de dispositivos: `%USERPROFILE%\AndroidTools\devices.json`.
- Copias del registro: `%USERPROFILE%\AndroidTools\backups\`.
- Perfiles: `%USERPROFILE%\AndroidTools\sync-profiles.json`.
- Logs: `%USERPROFILE%\AndroidTools\logs\adb-tools-YYYY-MM-DD.log`.
- Transferencias: `%USERPROFILE%\Downloads\AndroidBackup\`, `AndroidDocuments`, `AndroidFullBackup`.
- Explorador: `%USERPROFILE%\Downloads\AndroidFiles\`.
- Backup avanzado: `%USERPROFILE%\AndroidBackup\Advanced\` y `FullAdvanced_YYYYMMDD_HHMMSS\`.

Los backups pueden contener SMS, contactos, llamadas, redes Wi-Fi, clipboard y otros datos personales. Protegerlos con ACL/cifrado y no compartirlos sin revisar su contenido.

Activar logging DEBUG en la sesión actual:

```powershell
Import-Module .\AndroidAdb.psm1 -Force
Initialize-AdbLog -EnableDebug
Get-AdbLogPath
```

## 8. Pruebas

Ejecutar la suite directamente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\tests\Run-Tests.ps1
```

Usar `-Passthru` para que el runner no cierre la consola con `exit 1` cuando existan fallos:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\tests\Run-Tests.ps1 -Passthru
```

La suite comprueba el módulo, selección de dispositivos, sanitización de rutas, logs, exportaciones y sintaxis de los scripts. Pester es opcional; si no está disponible, el runner usa una ejecución directa de fallback.

Estado observado durante la revisión actual: `38` pruebas pasaron y `1` falló en `AndroidAdb.Tests.ps1`:

- `Clear-CurrentAdbDevice` no dejó la selección completamente nula según la expectativa.

La suite de integridad/sintaxis pasó completa: `21/21`.

## 9. Errores frecuentes y solución

| Mensaje o síntoma | Causa probable | Acción |
|---|---|---|
| `adb no se encuentra en PATH` | Platform-Tools no instalado o PATH no actualizado. | Instalar Platform-Tools, agregar su carpeta al PATH, abrir una nueva consola y ejecutar `Get-Command adb`. |
| No hay dispositivos detectados | Cable, depuración o red no disponible. | Activar depuración, conectar USB o misma Wi-Fi y revisar `adb devices -l`. |
| `unauthorized` | No se aceptó la clave RSA. | Desbloquear el teléfono, aceptar el diálogo y repetir `adb devices`; si persiste, reconectar por USB y ejecutar `setup.ps1`. |
| `offline` | Endpoint perdido o servidor ADB en estado inconsistente. | `adb disconnect HOST:PUERTO`, `adb connect HOST:PUERTO` y continuar solo cuando aparezca `device`. |
| `more than one device/emulator` | Hay varios dispositivos disponibles. | Elegir en el menú o usar `adb -s SERIAL ...` en cada comando. |
| Pairing falla o aparece `No pairing code provided` | Se intentó QR o falta el código correcto. | Usar el código de pairing del teléfono; el flujo QR no se ejecuta desde esta CLI. |
| mDNS no encuentra `adb-tls-connect` | Misma red ausente, aislamiento Wi-Fi, firewall o Wireless Debugging apagado. | Verificar red, activar Wireless Debugging, probar `adb mdns services` y usar manualmente el puerto TLS mostrado por Android. |
| `connection refused` o `10013` en `5555` | IP/puerto incorrecto, firewall o método equivocado. | `Test-NetConnection`; `5555` solo es para TCP/IP legado. En Wireless Debugging no asumir `5555`. |
| `Android/data` o `/data` denegado | Restricciones de Android/Scoped Storage o falta de root. | Considerarlo esperado; usar carpetas compartidas como `DCIM`, `Download` o `Pictures`. |
| Backup termina `DENIED`, `EMPTY`, `PARTIAL` o `ERROR` | El proveedor de contenido o la versión/OEM no permite esa extracción. | Revisar el manifiesto y conservar solo las categorías `OK` como respaldadas. |
| No hay espacio durante `pull` o backup | Falta espacio en la PC o el teléfono. | Ejecutar `df -h /sdcard`, revisar el disco local y descargar por carpetas. |
| `scrcpy` no está disponible | No está instalado o no está en PATH. | Instalar scrcpy, agregarlo al PATH y verificar `Get-Command scrcpy`. |
| Script bloqueado por política | Política de ejecución o marca de archivo descargado. | Usar `RemoteSigned`; consultar `Get-ExecutionPolicy -List` y, si corresponde, `Unblock-File .\*.ps1`. |
| `ParserError` o caracteres extraños | Consola/código con codificación incompatible. | Usar Windows PowerShell 5.1, guardar scripts críticos en UTF-8 con BOM y revisar `tests\GUI.Integrity.Tests.ps1`. |
| `devices.json` no se puede leer | JSON corrupto, versión no admitida o permisos. | Ejecutar diagnóstico, conservar una copia de `AndroidTools\backups`, validar el JSON y no editarlo mientras haya procesos activos. |
| Pruebas fallan en log o selección | Fallo conocido del estado actual del repositorio. | Revisar la salida de `Run-Tests.ps1`; no confundirlo con un fallo de conexión ADB. |

## 10. Checklist antes de transferir o respaldar

- `adb devices -l` muestra el teléfono correcto.
- El estado es `device`.
- Se usa el serial correcto con `-s` si hay varios dispositivos.
- La PC y el teléfono siguen en la misma red cuando la conexión es inalámbrica.
- Hay espacio disponible local y remoto.
- El destino del backup está protegido.
- En backup avanzado se revisa el manifiesto y no solo el mensaje final.
- Los errores quedan registrados en `%USERPROFILE%\AndroidTools\logs\`.
## Reconexión WiFi y dispositivos guardados

El registro de dispositivos conserva únicamente conexiones WiFi con una dirección `host:puerto`. Las conexiones USB se usan de forma temporal y no se guardan. Al abrir la GUI, los registros USB antiguos se migran y se crea una copia de respaldo antes de limpiarlos.

La reconexión automática intenta primero la última dirección guardada. En dispositivos configurados con Wireless Debugging también puede localizar el nuevo endpoint mediante mDNS. Estar en la misma red no garantiza que un teléfono configurado con el modo TCP/IP legado `5555` conserve la misma IP ni que el puerto siga activo después de reiniciar; en ese caso hay que conectar por USB y volver a activar `adb tcpip 5555`, o configurar Wireless Debugging mediante pairing para una reconexión más estable.

Las extracciones se organizan por dispositivo dentro de `Descargas\AndroidBackup`, usando el nombre amigable y el endpoint del teléfono. Así las fotos, documentos, descargas y backups de distintos dispositivos no se mezclan.

