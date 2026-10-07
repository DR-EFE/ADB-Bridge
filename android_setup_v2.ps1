# Configuracion ADB por Wireless Debugging moderno o TCP/IP USB legado.
# Compatible con Windows PowerShell 5.1.

. (Join-Path -Path $PSScriptRoot -ChildPath 'android_config.ps1')
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'android_setup_v2.ps1' -Message 'Inicio de configuracion.'

function Write-SetupTitle {
    param([string]$Text)
    Write-Host ''
    Write-Host '========================================' -ForegroundColor Cyan
    Write-Host ("   {0}" -f $Text) -ForegroundColor Yellow
    Write-Host '========================================' -ForegroundColor Cyan
    Write-Host ''
}

function Read-ValidPort {
    param([string]$Prompt)

    do {
        $value = Read-Host $Prompt
        $port = 0
        $valid = [int]::TryParse($value, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$port)
        if (-not $valid -or $port -lt 1 -or $port -gt 65535) {
            Write-Host 'El puerto debe ser un numero entre 1 y 65535.' -ForegroundColor Red
            $valid = $false
        }
    } while (-not $valid)

    return $port
}

function Read-ValidHost {
    param([string]$Prompt)

    do {
        $value = (Read-Host $Prompt).Trim()
        if ([string]::IsNullOrWhiteSpace($value) -or ($value.Contains(':') -and -not $value.StartsWith('['))) {
            Write-Host 'Indica un host o una direccion IPv4 valida, sin protocolo.' -ForegroundColor Red
            $value = $null
        }
    } while ([string]::IsNullOrWhiteSpace($value))

    return $value
}

function Read-FriendlyName {
    do { $value = (Read-Host 'Nombre amigable para recordar este dispositivo').Trim() }
    while ([string]::IsNullOrWhiteSpace($value))
    return $value
}

function Get-ReadySerialFromUsb {
    $records = @(Get-AndroidDeviceRecords | Where-Object {
        $_.Status -ceq 'device' -and $_.Serial -notmatch '^[^:]+:\d+$'
    })

    if ($records.Count -eq 0) {
        throw 'No se encontro un dispositivo USB autorizado en estado device.'
    }

    if ($records.Count -eq 1) {
        return $records[0].Serial
    }

    Write-Host 'Hay varios dispositivos USB autorizados:' -ForegroundColor Yellow
    for ($index = 0; $index -lt $records.Count; $index++) {
        Write-Host ("[{0}] {1}" -f ($index + 1), $records[$index].Serial) -ForegroundColor Gray
    }

    do {
        $selection = 0
        $valid = [int]::TryParse((Read-Host 'Selecciona el dispositivo'), [ref]$selection)
    } while (-not $valid -or $selection -lt 1 -or $selection -gt $records.Count)

    return $records[$selection - 1].Serial
}

function Get-DeviceIpFromUsb {
    param([string]$Serial)

    $routeOutput = @(Invoke-AdbCommand -Serial $Serial -Arguments @('shell', 'ip', 'route'))
    foreach ($line in $routeOutput) {
        $match = [regex]::Match([string]$line, '\bsrc\s+(?<ip>\d{1,3}(?:\.\d{1,3}){3})\b')
        if ($match.Success) {
            return $match.Groups['ip'].Value
        }
    }

    $addressOutput = @(Invoke-AdbCommand -Serial $Serial -Arguments @('shell', 'ip', 'addr'))
    foreach ($line in $addressOutput) {
        $match = [regex]::Match([string]$line, '\binet\s+(?<ip>\d{1,3}(?:\.\d{1,3}){3})/')
        if ($match.Success -and $match.Groups['ip'].Value -notlike '127.*') {
            return $match.Groups['ip'].Value
        }
    }

    return $null
}

function Connect-And-Save {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,

        [Parameter(Mandatory = $true)]
        [int]$Port,

        [Parameter(Mandatory = $true)]
        [ValidateSet('ModernPairing', 'LegacyTcpIp')]
        [string]$Mode,

        [Parameter(Mandatory = $true)]
        [string]$FriendlyName,

        [string]$UsbSerial,

        [bool]$IsPaired = $false
    )

    $endpoint = if ($HostName.Contains(':')) {
        "[{0}]:{1}" -f $HostName.Trim('[', ']'), $Port
    } else {
        "{0}:{1}" -f $HostName, $Port
    }

    Write-Host ("Conectando a {0}..." -f $endpoint) -ForegroundColor Green
    $connectTimeout = if ($Mode -eq 'LegacyTcpIp') { 30 } else { 15 }
    $record = Connect-AdbEndpoint -Endpoint $endpoint -TimeoutSeconds $connectTimeout

    $configPath = Save-AndroidConfig -HostName $HostName -Port $Port -Endpoint $endpoint -Mode $Mode -Serial $record.Serial
    $storedMode = if ($Mode -eq 'ModernPairing') { 'wireless-debugging-tls' } else { 'tcpip-5555' }
    $savedDevices = @(Get-SavedAdbDevices)
    $existing = @($savedDevices | Where-Object {
        ($UsbSerial -and $_.UsbSerial -ceq $UsbSerial) -or $_.LastEndpoint -ieq $record.Serial
    })[0]
    if ($null -eq $existing) {
        $sameName = @($savedDevices | Where-Object { $_.FriendlyName -ieq $FriendlyName })
        if ($sameName.Count -eq 1) { $existing = $sameName[0] }
    }
    if ($null -eq $existing) {
        $saved = Add-SavedAdbDevice -FriendlyName $FriendlyName -Mode $storedMode -UsbSerial $UsbSerial -LastEndpoint $record.Serial -IsPaired $IsPaired -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
    } elseif ($UsbSerial) {
        $saved = Update-SavedAdbDevice -Id $existing.Id -FriendlyName $FriendlyName -UsbSerial $UsbSerial -LastEndpoint $record.Serial -Mode $storedMode -IsPaired $IsPaired -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
    } else {
        $saved = Update-SavedAdbDevice -Id $existing.Id -FriendlyName $FriendlyName -LastEndpoint $record.Serial -Mode $storedMode -IsPaired $IsPaired -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
    }
    Write-Host ("Conexion valida. Estado: {0}" -f $record.Status) -ForegroundColor Green
    Write-Host ("Configuracion JSON guardada en: {0}" -f $configPath) -ForegroundColor Green
    Write-Host ("Dispositivo guardado en devices.json: {0} [{1}]" -f $saved.FriendlyName,$saved.Id) -ForegroundColor Green
    return $record
}

Write-SetupTitle 'ANDROID WIFI SETUP'

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Write-Host 'ERROR: adb no se encuentra en PATH.' -ForegroundColor Red
    exit 1
}

Write-Host 'ADB disponible:' -ForegroundColor Gray
Invoke-AdbHost -Arguments @('version') | Out-Host
Write-Host ''
Write-Host '[1] Wireless Debugging moderno (codigo de pairing)' -ForegroundColor Green
Write-Host '[2] TCP/IP USB legado (usa adb tcpip 5555)' -ForegroundColor Yellow
Write-Host ''
$method = Read-Host 'Selecciona una opcion (1 o 2)'

try {
    switch ($method) {
        '1' {
            Write-SetupTitle 'PAIRING MODERNO'
            $friendlyName = Read-FriendlyName
            Write-Host 'En el telefono abre Opciones de desarrollador > Depuracion inalambrica.' -ForegroundColor White
            Write-Host 'Selecciona Vincular dispositivo con codigo de emparejamiento.' -ForegroundColor White
            Write-Host 'El flujo QR no se ejecuta desde esta CLI; usa el codigo de pairing que muestra el telefono.' -ForegroundColor Yellow
            Write-Host ''

            $pairingHost = Read-ValidHost 'Host/IP mostrado para pairing'
            $pairingPort = Read-ValidPort 'Puerto de pairing'
            $pairingCode = (Read-Host 'Codigo de pairing').Trim()
            if ($pairingCode -notmatch '^\d{6}$') {
                throw 'El codigo de pairing debe contener exactamente 6 digitos.'
            }

            $pairingEndpoint = if ($pairingHost.Contains(':')) {
                "[{0}]:{1}" -f $pairingHost.Trim('[', ']'), $pairingPort
            } else {
                "{0}:{1}" -f $pairingHost, $pairingPort
            }

            Write-Host ("Ejecutando: adb pair {0} <codigo>" -f $pairingEndpoint) -ForegroundColor Green
            $pairOutput = @(Invoke-AdbHost -Arguments @('pair',$pairingEndpoint,$pairingCode))
            Write-Host ($pairOutput -join [Environment]::NewLine) -ForegroundColor Gray
            if (($pairOutput -join ' ') -match '(?i)failed|error') {
                throw 'El pairing fallo. Revisa IP, puerto y codigo en el telefono.'
            }

            Write-Host ''
            Write-Host 'Buscando el puerto de conexion mediante mDNS...' -ForegroundColor Cyan
            $connectionParts = $null
            for ($attempt = 1; $attempt -le 5 -and $null -eq $connectionParts; $attempt++) {
                $connectionParts = Get-AdbMdnsConnectionEndpoint -PreferredHost $pairingHost
                if ($null -eq $connectionParts -and $attempt -lt 5) {
                    Start-Sleep -Seconds 2
                }
            }

            if ($null -eq $connectionParts) {
                Write-Host 'mDNS no encontro el servicio de conexion.' -ForegroundColor Yellow
                $connectionHost = Read-ValidHost 'Host/IP para conexion (normalmente el mismo del pairing)'
                $connectionPort = Read-ValidPort 'Puerto de conexion mostrado en Depuracion inalambrica'
            } else {
                $connectionHost = $connectionParts.Host
                $connectionPort = $connectionParts.Port
                Write-Host ("mDNS encontro el endpoint de conexion: {0}" -f $connectionParts.Endpoint) -ForegroundColor Green
            }

            Connect-And-Save -HostName $connectionHost -Port $connectionPort -Mode 'ModernPairing' -FriendlyName $friendlyName -IsPaired $true | Out-Null
        }

        '2' {
            Write-SetupTitle 'TCP/IP USB LEGADO'
            Write-Host 'Conecta el telefono por USB y acepta la autorizacion ADB.' -ForegroundColor White
            Read-Host 'Presiona Enter cuando el telefono este conectado'

            $usbSerial = Get-ReadySerialFromUsb
            Write-Host ("Dispositivo USB: {0}" -f $usbSerial) -ForegroundColor Green
            $friendlyName = Read-FriendlyName

            $deviceIp = Get-DeviceIpFromUsb -Serial $usbSerial
            if ([string]::IsNullOrWhiteSpace($deviceIp)) {
                $deviceIp = Read-ValidHost 'No se pudo detectar la IP. Introduce la IP del telefono'
            } else {
                Write-Host ("IP detectada: {0}" -f $deviceIp) -ForegroundColor Green
            }

            Write-Host 'Activando TCP/IP legado en el puerto 5555...' -ForegroundColor Green
            $tcpipOutput = @(Invoke-AdbCommand -Serial $usbSerial -Arguments @('tcpip', '5555'))
            Write-Host ($tcpipOutput -join [Environment]::NewLine) -ForegroundColor Gray

            Start-Sleep -Seconds 5
            Connect-And-Save -HostName $deviceIp -Port 5555 -Mode 'LegacyTcpIp' -FriendlyName $friendlyName -UsbSerial $usbSerial -IsPaired $false | Out-Null
        }

        default {
            throw 'Opcion no valida. Ejecuta el script y selecciona 1 o 2.'
        }
    }
} catch {
    Write-Host ''
    Write-AdbLog -Level ERROR -Source 'android_setup_v2.ps1' -Message $_.Exception.Message
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-AdbLog -Level SUCCESS -Source 'android_setup_v2.ps1' -Message 'Configuracion completada.'
Write-Host 'Configuracion completada. Ya puedes desconectar el USB si corresponde.' -ForegroundColor Green
Read-Host 'Presiona Enter para salir'
exit 0
