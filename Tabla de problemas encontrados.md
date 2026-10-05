## Resumen ejecutivo

- El flujo moderno de Wireless Debugging está roto: usa `adb pair --qr` y fuerza `:5555`, aunque el puerto de conexión moderno es distinto al puerto de pairing.
- `SETUP.bat` llama a `android_setup.ps1`, archivo inexistente; el archivo real es `setup.ps1`.
- `AutorizacionWeb.ps1` contiene un error de análisis en Windows PowerShell 5.1.
- Ningún menú selecciona ni propaga correctamente el dispositivo mediante `adb -s`.
- Varios scripts confirman éxito aunque `adb pull`, `adb shell` o los respaldos hayan fallado.
- El backup avanzado no crea realmente un vCard y muchos datos no son accesibles sin permisos privilegiados/root.
- Existen inyecciones de comandos mediante búsquedas/rutas introducidas al shell remoto.
- La arquitectura debe unificarse alrededor de un módulo común, configuración JSON, selección explícita de dispositivo y estados verificables.

La documentación oficial indica que Android 11+ usa pairing mediante código/QR, pero el comando CLI documentado es `adb pair ip:puerto`; el QR se obtiene mediante Android Studio. Después del pairing, la conexión usa mDNS o un puerto de conexión separado. [Documentación oficial de ADB](https://developer.android.com/tools/adb)

## Tabla de problemas encontrados

| Severidad | Archivo | Problema real | Corrección recomendada |
|---|---|---|---|
| Crítica | [SETUP.bat:16](C:/Users/pipe6/Downloads/Android/SETUP.bat:16) | Ejecuta `android_setup.ps1`, pero ese archivo no existe. | Cambiar a `setup.ps1` o renombrar consistentemente el script. |
| Crítica | [AutorizacionWeb.ps1:40](C:/Users/pipe6/Downloads/Android/AutorizacionWeb.ps1:40) | Windows PowerShell 5.1 reporta `The string is missing the terminator`. El archivo contiene texto UTF-8 corrupto/mojibake. | Guardar como UTF-8 válido y eliminar caracteres corruptos/emojis. |
| Crítica | [android_setup_v2.ps1:52](C:/Users/pipe6/Downloads/Android/android_setup_v2.ps1:52) | `adb pair --qr` no es un flujo CLI válido en el ADB instalado. En la auditoría local, ADB 36.0.0 devuelve `No pairing code provided`. | Eliminar esta opción o documentar QR mediante Android Studio. |
| Crítica | [android_setup_v2.ps1:110](C:/Users/pipe6/Downloads/Android/android_setup_v2.ps1:110) | Después de `adb pair`, conecta siempre a `IP:5555`. En Wireless Debugging el puerto de conexión no tiene por qué ser 5555. | Usar `adb mdns services` o pedir el puerto mostrado en Wireless Debugging. |
| Crítica | [android_reconnect.ps1:30](C:/Users/pipe6/Downloads/Android/android_reconnect.ps1:30) | La reconexión solo lee la IP; pierde el puerto moderno, el modo de conexión y el serial. | Guardar `host`, `port`, `mode`, `serial` y endpoint completo en JSON. |
| Alta | [android_setup_v2.ps1:83](C:/Users/pipe6/Downloads/Android/android_setup_v2.ps1:83) | Guarda la IP aunque pairing/conexión hayan fallado. | Comprobar `$LASTEXITCODE` y verificar estado exacto `device` antes de guardar. |
| Alta | [android_setup_v2.ps1:147](C:/Users/pipe6/Downloads/Android/android_setup_v2.ps1:147) | Con múltiples dispositivos USB, selecciona silenciosamente el primero. | Rechazar múltiples dispositivos o mostrar selector. |
| Alta | Todos los scripts | La mayoría ejecuta `adb shell`, `adb pull` y `adb push` sin `adb -s`. | Resolver un serial único y usarlo en cada comando. |
| Alta | [android_reconnect.ps1:41](C:/Users/pipe6/Downloads/Android/android_reconnect.ps1:41) | Considera éxito cualquier línea que contenga `IP:5555`, incluso `offline` o `unauthorized`. | Parsear `adb devices` y exigir estado exactamente `device`. |
| Alta | [android_transfer.ps1:60](C:/Users/pipe6/Downloads/Android/android_transfer.ps1:60) | Imprime “OK” sin comprobar el resultado de `adb pull`. Hay fallos silenciosos. | Centralizar ejecución ADB y lanzar error si el código de salida no es cero. |
| Alta | [android_transfer.ps1:273](C:/Users/pipe6/Downloads/Android/android_transfer.ps1:273) | Los `Start-Job` se esperan y eliminan sin `Receive-Job`; fallos de copia se pierden. Además, no copia `Pictures`. | Recibir resultados, validar estados y copiar todas las carpetas anunciadas. |
| Alta | [android_advanced_backup.ps1:57](C:/Users/pipe6/Downloads/Android/android_advanced_backup.ps1:57) | Genera `$fullPath` para un `.vcf`, pero nunca escribe ese archivo. Solo crea archivos de texto raw. | Eliminar “vCard” o implementar una exportación real. |
| Alta | [android_advanced_backup.ps1:61](C:/Users/pipe6/Downloads/Android/android_advanced_backup.ps1:61) | `content query` sobre contactos, SMS, llamadas y calendario puede devolver `SecurityException` o vacío; el script lo reporta como exitoso. | Registrar resultado por operación y marcar `DENIED`, `EMPTY`, `PARTIAL` o `OK`. |
| Alta | [android_advanced_backup.ps1:408](C:/Users/pipe6/Downloads/Android/android_advanced_backup.ps1:408) | El backup completo siempre genera un resumen que afirma que todos los datos fueron respaldados, aunque fallen. | Crear un manifiesto con estado y código de salida por categoría. |
| Media | [android_file_explorer.ps1:419](C:/Users/pipe6/Downloads/Android/android_file_explorer.ps1:419) | La opción `"d"` aparece dos veces: documentos y descargar. La opción `"h"` también aparece dos veces: WhatsApp e historial. | Separar comandos de acceso rápido y acciones, o usar claves distintas. |
| Media | [android_file_explorer.ps1:304](C:/Users/pipe6/Downloads/Android/android_file_explorer.ps1:304) | Validaciones permiten `0`; `$items[-1]` puede seleccionar el último elemento accidentalmente. | Exigir `>= 1` y `<= Count`. |
| Media | [android_file_explorer.ps1:69](C:/Users/pipe6/Downloads/Android/android_file_explorer.ps1:69) | Parser de `ls -la` demasiado rígido: propietarios, permisos, fechas y nombres pueden variar entre fabricantes. | Usar `find` con formato controlado o comandos separados para metadatos. |
| Media | [android_transfer.ps1:335](C:/Users/pipe6/Downloads/Android/android_transfer.ps1:335) | La búsqueda inserta texto del usuario dentro de una orden shell remota. Permite romper el quoting o ejecutar comandos. | Validar entrada y usar argumentos separados, no concatenación shell. |
| Media | [android_file_explorer.ps1:283](C:/Users/pipe6/Downloads/Android/android_file_explorer.ps1:283) | El término de búsqueda también se concatena directamente en `adb shell find`. | Escapar comillas o restringir caracteres permitidos. |
| Media | [android_list_downloadable.ps1:251](C:/Users/pipe6/Downloads/Android/android_list_downloadable.ps1:251) | `customName` se usa directamente como carpeta Windows; puede contener `..`, caracteres inválidos o nombres reservados. | Sanitizar nombres y confinarlos al directorio de backup. |
| Media | [AutorizacionWeb.ps1:12](C:/Users/pipe6/Downloads/Android/AutorizacionWeb.ps1:12) | IP fija `192.168.1.70:5555`; no funciona tras cambiar de red/IP y no corresponde necesariamente a Wireless Debugging moderno. | Leer configuración común o pedir endpoint. |
| Media | [android_setup_v2.ps1:126](C:/Users/pipe6/Downloads/Android/android_setup_v2.ps1:126) | `adb kill-server` rompe conexiones de otros dispositivos y procesos que usen ADB. | Evitarlo salvo recuperación explícita. |
| Media | [SETUP.bat:16](C:/Users/pipe6/Downloads/Android/SETUP.bat:16) | `-ExecutionPolicy Bypass` reduce la protección de PowerShell. | Usar scripts confiables, `-NoProfile` y una política documentada. |
| Baja | [android_list_downloadable.ps1:7](C:/Users/pipe6/Downloads/Android/android_list_downloadable.ps1:7) | El parámetro `$recursive` nunca se usa. | Eliminarlo o implementarlo. |
| Baja | [android_transfer.ps1:44](C:/Users/pipe6/Downloads/Android/android_transfer.ps1:44) | “Fotos recientes” descarga toda la cámara; no usa fecha. | Buscar archivos por fecha mediante `find` y validar resultado. |
| Baja | [read.md:9](C:/Users/pipe6/Downloads/Android/read.md:9) | Documentación apunta a `android_setup.ps1`, pero el archivo real es `setup.ps1`. | Corregir nombres y rutas. |

## Compatibilidad Android 11–15

| Versión | Evaluación |
|---|---|
| Android 11 | Wireless Debugging moderno disponible. El pairing requiere código o QR; el código de pairing y el puerto de conexión son conceptos distintos. Scoped Storage restringe `Android/data`, `Android/obb` y datos privados de otras apps. |
| Android 12 | `adb backup` excluye datos de aplicaciones por defecto para apps que apuntan a Android 12 o superior. Estos scripts no usan `adb backup`, pero sus `content query` no son un sustituto confiable. [Android 12](https://developer.android.com/about/versions/12/summary) |
| Android 13 | Las aplicaciones tienen permisos multimedia granulares, pero eso no convierte a ADB en un mecanismo universal de acceso. Clipboard, proveedores de contenido y datos de aplicaciones siguen sujetos a restricciones/OEM. |
| Android 14 | No elimina las restricciones de almacenamiento ni concede acceso a `/data/data/*`. Las operaciones sobre `/sdcard/DCIM`, `Download` y archivos compartidos suelen funcionar si ADB está autorizado. |
| Android 15 | Mantiene el modelo de Wireless Debugging emparejado y las restricciones de datos privados. El resultado depende especialmente de fabricante, SELinux y versión de Platform-Tools. |

Android 11 restringe explícitamente el acceso de aplicaciones a directorios privados de otras aplicaciones y bloquea el acceso mediante SAF a `Android/data` y `Android/obb`; por eso esas descargas deben considerarse opcionales y parciales. [Storage en Android 11](https://developer.android.com/about/versions/11/privacy/storage)

La documentación oficial recomienda usar la versión más reciente de Platform-Tools y señala que las versiones actuales son retrocompatibles. La máquina auditada tiene ADB `36.0.0`; conviene actualizarlo y validar con `adb version` y `adb server-status`. [Platform-Tools](https://developer.android.com/tools/releases/platform-tools)

## Correcciones críticas

### 1. Resolver dispositivos y estados correctamente

```powershell
$adbCommand = (Get-Command adb -ErrorAction Stop).Source

function Get-AdbDevices {
    $lines = @(& $script:adbCommand devices -l 2>&1)
    $result = @()

    foreach ($line in $lines) {
        if ($line -match '^(?<serial>\S+)\s+(?<state>device|offline|unauthorized|no permissions)(?:\s+.*)?$') {
            $result += [pscustomobject]@{
                Serial = $matches.serial
                State  = $matches.state
            }
        }
    }

    return $result
}

function Get-SingleReadyDevice {
    $ready = @(Get-AdbDevices | Where-Object State -eq 'device')

    if ($ready.Count -eq 0) {
        $states = @(Get-AdbDevices)
        throw "No hay un dispositivo ADB autorizado. Estados: $($states.State -join ', ')"
    }

    if ($ready.Count -gt 1) {
        throw "Hay múltiples dispositivos. Selecciona uno mediante -s: $($ready.Serial -join ', ')"
    }

    return $ready[0].Serial
}

function Invoke-Adb {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,

        [string]$Serial
    )

    $args = @()
    if ($Serial) {
        $args += @('-s', $Serial)
    }
    $args += $Arguments

    $output = @(& $script:adbCommand @args 2>&1)
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "ADB falló con código $exitCode: $($output -join [Environment]::NewLine)"
    }

    return $output
}
```

Uso:

```powershell
$serial = Get-SingleReadyDevice
Invoke-Adb -Serial $serial -Arguments @('shell', 'getprop', 'ro.product.model')
```

### 2. Pairing moderno sin asumir `5555`

```powershell
$ip = Read-Host 'IP del teléfono'
$pairingPort = Read-Host 'Puerto de pairing'
$pairingCode = Read-Host 'Código de pairing'

$pairOutput = @(
    & $adbCommand pair "$ip`:$pairingPort" $pairingCode 2>&1
)

if ($LASTEXITCODE -ne 0 -or ($pairOutput -join "`n") -notmatch '(?i)successfully paired') {
    throw "El pairing falló: $($pairOutput -join ' ')"
}

$services = @(& $adbCommand mdns services 2>&1)

$service = $services |
    Where-Object {
        $_ -match '_adb-tls-connect\._tcp' -and
        $_ -match ([regex]::Escape($ip) + ':(?<port>\d+)\s*$')
    } |
    Select-Object -First 1

if ($service -and $service -match ':(?<port>\d+)\s*$') {
    $connectPort = [int]$matches.port
} else {
    $connectPort = [int](Read-Host 'Puerto de conexión mostrado en Wireless Debugging')
}

$endpoint = "$ip`:$connectPort"
$connectOutput = @(& $adbCommand connect $endpoint 2>&1)

if ($LASTEXITCODE -ne 0) {
    throw "No se pudo conectar a $endpoint`: $($connectOutput -join ' ')"
}

$connected = @(Get-AdbDevices | Where-Object {
    $_.Serial -eq $endpoint -and $_.State -eq 'device'
})

if ($connected.Count -ne 1) {
    throw "El endpoint $endpoint no terminó en estado device."
}
```

El puerto de pairing no debe reutilizarse como puerto de conexión, y `5555` debe reservarse para el método antiguo `adb tcpip 5555`.

### 3. Configuración persistente correcta

```powershell
$configDir = Join-Path $env:USERPROFILE 'AndroidTools'
$configFile = Join-Path $configDir 'android_config.json'

New-Item -ItemType Directory -Path $configDir -Force | Out-Null

[pscustomobject]@{
    Host       = $ip
    Port       = $connectPort
    Endpoint   = $endpoint
    Mode       = 'wireless-debugging-tls'
    UpdatedUtc = [DateTime]::UtcNow.ToString('o')
} |
ConvertTo-Json |
Set-Content -Path $configFile -Encoding UTF8
```

La reconexión debe leer `Endpoint`, ejecutar `adb connect`, y exigir estado `device`; no debe usar una IP y puerto reconstruidos manualmente.

## Riesgos de seguridad

- Los respaldos contienen SMS, contactos, llamadas, redes Wi-Fi, configuraciones, clipboard y dumps de sistema en texto plano.
- `dumpsys clipboard`, `dumpsys wifi` y `dumpsys connectivity` pueden contener secretos, tokens, SSID, direcciones y datos personales.
- `-ExecutionPolicy Bypass` facilita ejecutar scripts modificados si la carpeta no es confiable.
- Las entradas de búsqueda y rutas se interpolan en comandos remotos.
- No hay control de integridad, cifrado, retención ni permisos NTFS del directorio de backup.
- `adb kill-server` puede afectar otros procesos o dispositivos conectados.

## Mejoras prioritarias

### Alta

1. Crear un módulo común `AndroidAdb.psm1`.
2. Usar siempre `adb -s <serial>`.
3. Separar claramente:
   - Wireless Debugging moderno: `adb pair` + mDNS/puerto TLS.
   - TCP/IP antiguo: `adb tcpip 5555`.
4. Reemplazar el archivo de texto de configuración por JSON.
5. Centralizar errores y comprobar cada código de salida.
6. Eliminar afirmaciones falsas de “vCard”, “backup completo” y “contraseñas respaldadas”.
7. Implementar manifiesto de backup por categoría.
8. Corregir `SETUP.bat` y `AutorizacionWeb.ps1`.

### Media

1. Validar y sanitizar rutas/nombres.
2. Eliminar `adb kill-server` del flujo normal.
3. Añadir selector de dispositivos.
4. Reemplazar parsing de `ls -la`.
5. Usar UTF-8 consistente.
6. Añadir pruebas para Android 11, 12, 13, 14 y 15, además de Samsung/Pixel.
7. Proteger respaldos con ACL NTFS y cifrado externo.

### Baja

1. Unificar nombres de scripts.
2. Eliminar parámetros sin uso.
3. Corregir mensajes que prometen funciones no implementadas.
4. Quitar `pause` duplicados de los `.bat`.
5. Sustituir menús duplicados por un único punto de entrada.

La funcionalidad de transferencia de archivos compartidos puede conservarse, pero el backup avanzado debe presentarse como una colección de extracciones best-effort, no como un respaldo completo sin root.