# Administrador y reconexion inteligente de dispositivos ADB guardados.
# Compatible con Windows PowerShell 5.1.
[CmdletBinding()]
param([switch]$NoMenu)

Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force

function Read-RequiredText {
    param([string]$Prompt)
    do { $value = (Read-Host $Prompt).Trim() } while ([string]::IsNullOrWhiteSpace($value))
    return $value
}

function Read-ValidPort {
    param([string]$Prompt,[int]$DefaultPort = 0)
    do {
        $text = (Read-Host $Prompt).Trim()
        if ([string]::IsNullOrWhiteSpace($text) -and $DefaultPort -gt 0) { return $DefaultPort }
        $port = 0
        $valid = [int]::TryParse($text,[ref]$port) -and $port -ge 1 -and $port -le 65535
        if (-not $valid) { Write-Host 'Puerto invalido; usa un numero entre 1 y 65535.' -ForegroundColor Red }
    } while (-not $valid)
    return $port
}

function Show-SavedDevices {
    param([object[]]$Devices = @(Get-SavedAdbDevices))
    Write-Host ''
    Write-Host 'DISPOSITIVOS GUARDADOS' -ForegroundColor Cyan
    if ($Devices.Count -eq 0) { Write-Host '  No hay dispositivos guardados.' -ForegroundColor Yellow; return }
    for ($index=0; $index -lt $Devices.Count; $index++) {
        $device = $Devices[$index]
        $endpoint = if ($device.LastEndpoint) { $device.LastEndpoint } else { '(sin endpoint)' }
        $last = if ($device.LastConnectedUtc) { $device.LastConnectedUtc } else { '(nunca)' }
        Write-Host ("[{0}] {1}" -f ($index+1),$device.FriendlyName) -ForegroundColor Green
        Write-Host ("    Id: {0}" -f $device.Id) -ForegroundColor DarkGray
        Write-Host ("    Modo: {0} | Endpoint: {1}" -f $device.Mode,$endpoint) -ForegroundColor Gray
        Write-Host ("    USB: {0} | Ultima conexion UTC: {1} | Paired: {2}" -f $device.UsbSerial,$last,$device.IsPaired) -ForegroundColor DarkGray
    }
}

function Select-SavedDevice {
    param([string]$Prompt = 'Selecciona dispositivo')
    $devices = @(Get-SavedAdbDevices)
    Show-SavedDevices -Devices $devices
    if ($devices.Count -eq 0) { return $null }
    do {
        $selection = 0
        $valid = [int]::TryParse((Read-Host $Prompt),[ref]$selection)
    } while (-not $valid -or $selection -lt 1 -or $selection -gt $devices.Count)
    return $devices[$selection-1]
}

function Read-ManualEndpoint {
    param([string]$Mode)
    $hostName = Read-RequiredText 'Nueva IP o nombre de host'
    $defaultPort = if ($Mode -eq 'tcpip-5555') { 5555 } else { 0 }
    $prompt = if ($defaultPort) { 'Puerto [5555]' } else { 'Puerto de conexion mostrado en Wireless Debugging' }
    $port = Read-ValidPort -Prompt $prompt -DefaultPort $defaultPort
    $candidate = if ($hostName.Contains(':')) { "[{0}]:{1}" -f $hostName.Trim('[',']'),$port } else { "{0}:{1}" -f $hostName,$port }
    return (ConvertTo-AdbEndpoint $candidate).Endpoint
}

function Select-MdnsEndpoint {
    param([object]$Device)
    try { $services = @(Get-AdbMdnsServices) }
    catch { Write-Host ("mDNS no disponible: {0}" -f $_.Exception.Message) -ForegroundColor DarkYellow; return $null }
    if ($services.Count -eq 0) { Write-Host 'mDNS no encontro servicios adb-tls-connect.' -ForegroundColor Yellow; return $null }

    if ($Device.UsbSerial) {
        $matching = @($services | Where-Object {
            $_.ServiceName -and $_.ServiceName.IndexOf([string]$Device.UsbSerial,[StringComparison]::OrdinalIgnoreCase) -ge 0
        })
        if ($matching.Count -eq 1) { return $matching[0].Endpoint }
    }
    if ($Device.LastEndpoint) {
        try { $oldHost = (ConvertTo-AdbEndpoint $Device.LastEndpoint).Host } catch { $oldHost = $null }
        $matching = @($services | Where-Object { $oldHost -and $_.Host -ieq $oldHost })
        if ($matching.Count -eq 1) { return $matching[0].Endpoint }
    }
    if ($services.Count -eq 1) { return $services[0].Endpoint }

    Write-Host 'mDNS encontro varios dispositivos:' -ForegroundColor Cyan
    for ($index=0; $index -lt $services.Count; $index++) {
        Write-Host ("[{0}] {1} ({2})" -f ($index+1),$services[$index].Endpoint,$services[$index].ServiceName)
    }
    $text = (Read-Host 'Selecciona uno o Enter para introducirlo manualmente').Trim()
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    $selection = 0
    if (-not [int]::TryParse($text,[ref]$selection) -or $selection -lt 1 -or $selection -gt $services.Count) {
        Write-Host 'Seleccion mDNS invalida.' -ForegroundColor Yellow
        return $null
    }
    return $services[$selection-1].Endpoint
}

function Connect-And-RememberDevice {
    param([object]$Device,[string]$Endpoint)
    Write-Host ("Conectando {0} a {1}..." -f $Device.FriendlyName,$Endpoint) -ForegroundColor Cyan
    try { $record = Connect-AdbEndpoint -Endpoint $Endpoint -TimeoutSeconds 10 }
    catch { Write-Host ("Fallo: {0}" -f $_.Exception.Message) -ForegroundColor Yellow; return $null }
    Update-SavedAdbDevice -Id $Device.Id -LastEndpoint $record.Serial -LastConnectedUtc ([DateTime]::UtcNow.ToString('o')) | Out-Null
    Write-Host ("OK: {0} conectado en estado device ({1})." -f $Device.FriendlyName,$record.Serial) -ForegroundColor Green
    return $record
}

function Invoke-SmartReconnect {
    [CmdletBinding()]
    param([string]$DeviceId)

    $device = if ($DeviceId) { @(Get-SavedAdbDevices | Where-Object { $_.Id -ceq $DeviceId })[0] } else { Select-SavedDevice }
    if ($null -eq $device) { throw 'No hay un dispositivo guardado para reconectar.' }

    if ($device.LastEndpoint) {
        $record = Connect-And-RememberDevice -Device $device -Endpoint $device.LastEndpoint
        if ($null -ne $record) { return $record }
    }
    if ($device.Mode -eq 'wireless-debugging-tls') {
        Write-Host 'Buscando el nuevo endpoint mediante mDNS...' -ForegroundColor Cyan
        $mdnsEndpoint = Select-MdnsEndpoint -Device $device
        if ($mdnsEndpoint) {
            $record = Connect-And-RememberDevice -Device $device -Endpoint $mdnsEndpoint
            if ($null -ne $record) { return $record }
        }
    } elseif ($device.Mode -eq 'tcpip-5555' -and $device.UsbSerial) {
        $usbRecord = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $device.UsbSerial })[0]
        if ($null -ne $usbRecord -and $usbRecord.Status -ceq 'device') {
            Write-Host 'USB detectado; obteniendo la IP nueva automaticamente...' -ForegroundColor Cyan
            $usbIp = Get-AdbDeviceIpv4 -Serial $device.UsbSerial
            if ($usbIp) {
                Invoke-Adb -Serial $device.UsbSerial -Arguments @('tcpip','5555') | Out-Host
                Start-Sleep -Seconds 2
                $usbEndpoint = (ConvertTo-AdbEndpoint ("{0}:5555" -f $usbIp)).Endpoint
                $record = Connect-And-RememberDevice -Device $device -Endpoint $usbEndpoint
                if ($null -ne $record) { return $record }
            }
        }
    }

    Write-Host 'No fue posible reconectar automaticamente.' -ForegroundColor Yellow
    $manualEndpoint = Read-ManualEndpoint -Mode $device.Mode
    $record = Connect-And-RememberDevice -Device $device -Endpoint $manualEndpoint
    if ($null -eq $record) { throw "No se pudo conectar '$($device.FriendlyName)' al nuevo endpoint." }
    return $record
}

function Select-ReadyUsbDevice {
    $devices = @(Get-AdbDevices | Where-Object { $_.Status -ceq 'device' -and $_.Serial -notmatch '^\[?.+\]?:\d+$' })
    if ($devices.Count -eq 0) { throw 'No hay dispositivos USB autorizados en estado device.' }
    if ($devices.Count -eq 1) { return $devices[0] }
    for ($index=0; $index -lt $devices.Count; $index++) { Write-Host ("[{0}] {1}" -f ($index+1),$devices[$index].Serial) }
    do { $selection=0; $valid=[int]::TryParse((Read-Host 'Selecciona USB'),[ref]$selection) } while(-not $valid -or $selection -lt 1 -or $selection -gt $devices.Count)
    return $devices[$selection-1]
}

function Add-WirelessDevice {
    $name = Read-RequiredText 'Nombre amigable'
    $pairHost = Read-RequiredText 'IP/host mostrado para pairing'
    $pairPort = Read-ValidPort 'Puerto de pairing'
    $pairCode = Read-RequiredText 'Codigo de pairing'
    if ($pairCode -notmatch '^\d{6}$') { throw 'El codigo de pairing debe contener exactamente 6 digitos.' }
    $pairCandidate = if ($pairHost.Contains(':')) { "[{0}]:{1}" -f $pairHost.Trim('[',']'),$pairPort } else { "{0}:{1}" -f $pairHost,$pairPort }
    $pairEndpoint = (ConvertTo-AdbEndpoint $pairCandidate).Endpoint
    $pairOutput = @(Invoke-AdbHost -Arguments @('pair',$pairEndpoint,$pairCode))
    if (($pairOutput -join ' ') -match '(?i)failed|error') { throw "El pairing fallo: $($pairOutput -join ' ')" }
    $temporary = [pscustomobject]@{ UsbSerial=$null; LastEndpoint=$null }
    $endpoint = Select-MdnsEndpoint -Device $temporary
    if (-not $endpoint) { $endpoint = Read-ManualEndpoint -Mode 'wireless-debugging-tls' }
    $record = Connect-AdbEndpoint -Endpoint $endpoint -TimeoutSeconds 15
    $device = Add-SavedAdbDevice -FriendlyName $name -Mode 'wireless-debugging-tls' -LastEndpoint $record.Serial -IsPaired $true -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
    Write-Host ("Dispositivo guardado: {0} [{1}]" -f $device.FriendlyName,$device.Id) -ForegroundColor Green
}

function Add-LegacyUsbDevice {
    $usb = Select-ReadyUsbDevice
    $name = Read-RequiredText 'Nombre amigable'
    $ip = Get-AdbDeviceIpv4 -Serial $usb.Serial
    if (-not $ip) { $ip = Read-RequiredText 'IP actual del telefono' }
    Invoke-Adb -Serial $usb.Serial -Arguments @('tcpip','5555') | Out-Host
    Start-Sleep -Seconds 2
    $endpoint = (ConvertTo-AdbEndpoint ("{0}:5555" -f $ip)).Endpoint
    $record = Connect-AdbEndpoint -Endpoint $endpoint -TimeoutSeconds 15
    $device = Add-SavedAdbDevice -FriendlyName $name -Mode 'tcpip-5555' -UsbSerial $usb.Serial -LastEndpoint $record.Serial -IsPaired $false -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
    Write-Host ("Dispositivo guardado: {0} [{1}]" -f $device.FriendlyName,$device.Id) -ForegroundColor Green
}

function Add-SavedDeviceInteractive {
    Write-Host '[1] Wireless Debugging TLS (pair + connect)'
    Write-Host '[2] USB + tcpip 5555 legado'
    switch (Read-Host 'Metodo') { '1' { Add-WirelessDevice }; '2' { Add-LegacyUsbDevice }; default { throw 'Metodo invalido.' } }
}

function Rename-SavedDeviceInteractive {
    $device = Select-SavedDevice -Prompt 'Dispositivo a renombrar'
    if ($null -eq $device) { return }
    $newName = Read-RequiredText 'Nuevo nombre'
    Update-SavedAdbDevice -Id $device.Id -FriendlyName $newName | Out-Null
    Write-Host 'Nombre actualizado.' -ForegroundColor Green
}

function Remove-SavedDeviceInteractive {
    $device = Select-SavedDevice -Prompt 'Dispositivo a eliminar'
    if ($null -eq $device) { return }
    if ((Read-Host ("Escribe ELIMINAR para borrar '{0}'" -f $device.FriendlyName)) -cne 'ELIMINAR') { Write-Host 'Cancelado.'; return }
    Remove-SavedAdbDevice -Id $device.Id | Out-Null
    Write-Host 'Dispositivo eliminado de devices.json.' -ForegroundColor Green
}

function Start-AdbDeviceManager {
    Test-AdbAvailable | Out-Null
    while ($true) {
        Write-Host ''
        Write-Host '========================================' -ForegroundColor Cyan
        Write-Host ' ADMINISTRADOR DE DISPOSITIVOS ANDROID' -ForegroundColor Yellow
        Write-Host '========================================' -ForegroundColor Cyan
        Write-Host '[1] Reconectar dispositivo guardado'
        Write-Host '[2] Agregar dispositivo'
        Write-Host '[3] Renombrar dispositivo'
        Write-Host '[4] Eliminar dispositivo'
        Write-Host '[5] Ver dispositivos y endpoints'
        Write-Host '[6] Salir'
        try {
            switch (Read-Host 'Opcion') {
                '1' { Invoke-SmartReconnect | Out-Null }
                '2' { Add-SavedDeviceInteractive }
                '3' { Rename-SavedDeviceInteractive }
                '4' { Remove-SavedDeviceInteractive }
                '5' { Show-SavedDevices }
                '6' { return }
                default { Write-Host 'Opcion invalida.' -ForegroundColor Red }
            }
        } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red }
    }
}

if (-not $NoMenu) { Start-AdbDeviceManager }
