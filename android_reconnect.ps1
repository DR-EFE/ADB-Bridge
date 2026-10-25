# Administrador y reconexion inteligente de dispositivos ADB guardados.
# Compatible con Windows PowerShell 5.1.
[CmdletBinding()]
param([switch]$NoMenu)

Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'android_reconnect.ps1' -Message 'Inicio de administrador/reconexion.'

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
    param([object]$Device,[switch]$NonInteractive)
    try { $services = @(Get-AdbMdnsServices) }
    catch { Write-Host ("mDNS no disponible: {0}" -f $_.Exception.Message) -ForegroundColor DarkYellow; return $null }
    if ($services.Count -eq 0) { Write-Host 'mDNS no encontro servicios adb-tls-connect.' -ForegroundColor Yellow; return $null }

    if ($Device.PSObject.Properties['MdnsServiceName'] -and $Device.MdnsServiceName) {
        $matching = @($services | Where-Object { $_.ServiceName -ieq [string]$Device.MdnsServiceName })
        if ($matching.Count -eq 1) { return $matching[0].Endpoint }
    }

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
    if ($services.Count -eq 1 -and -not $Device.LastEndpoint) { return $services[0].Endpoint }

    if ($NonInteractive) { return $null }
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
    param([object]$Device,[string]$Endpoint,[int]$TimeoutSeconds=5,[string]$Source='reconnect',[switch]$BindIdentity)
    $started = [DateTime]::UtcNow
    Write-AdbLog -Level INFO -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $Endpoint -Message ("Intento de conexion para {0}." -f $Device.FriendlyName)
    Write-Host ("Conectando {0} a {1}..." -f $Device.FriendlyName,$Endpoint) -ForegroundColor Cyan
    try { $record = Connect-AdbEndpoint -Endpoint $Endpoint -TimeoutSeconds $TimeoutSeconds }
    catch {
        $detail = $_.Exception.Message
        $script:LastReconnectCause = if($detail -match '(?i)authenticat|certificate|unauthorized'){'AUTH_FAILED'}elseif($detail -match '(?i)no route|unreachable|vpn'){'NO_ROUTE_VPN'}elseif($detail -match '(?i)timed out|timeout'){'TIMEOUT'}else{'CONNECT_RECHAZADO'}
        Write-AdbLog -Level ERROR -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $Endpoint -Message ("{0}: {1}; etapa={2}; duracionMs={3}" -f $script:LastReconnectCause,$detail,$Source,[int]([DateTime]::UtcNow-$started).TotalMilliseconds)
        Write-Host ("Fallo: {0}" -f $detail) -ForegroundColor Yellow
        return $null
    }
    try { $transport = ((@(Invoke-Adb -Serial $record.Serial -Arguments @('get-serialno')) | ForEach-Object { [string]$_ }) -join '').Trim() }
    catch { $transport = $null }
    if ($transport -cne $record.Serial) {
        $script:LastReconnectCause = 'SERIAL_NO_COINCIDE'
        Write-AdbLog -Level ERROR -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $Endpoint -Message ("SERIAL_NO_COINCIDE: transporte inesperado; etapa={0}; duracionMs={1}" -f $Source,[int]([DateTime]::UtcNow-$started).TotalMilliseconds)
        return $null
    }
    $wifiId = Get-AdbPhysicalId -Serial $record.Serial
    $wifiHash = if ($wifiId) { Get-AdbPhysicalIdHash -PhysicalId $wifiId } else { $null }
    $expectedHash = [string]$Device.PhysicalIdHash
    if ($expectedHash -and ($null -eq $wifiHash -or $expectedHash -cne $wifiHash)) {
        $script:LastReconnectCause = 'SERIAL_NO_COINCIDE'
        Write-AdbLog -Level ERROR -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $Endpoint -Message ("SERIAL_NO_COINCIDE: identidad fisica inesperada; etapa={0}; duracionMs={1}" -f $Source,[int]([DateTime]::UtcNow-$started).TotalMilliseconds)
        return $null
    }
    if ($Device.UsbSerial) {
        $usb = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $Device.UsbSerial -and $_.Status -ceq 'device' })[0]
        if ($usb) {
            $usbId = Get-AdbPhysicalId -Serial $usb.Serial
            if (-not $usbId -or (Get-AdbPhysicalIdHash -PhysicalId $usbId) -cne $wifiHash) {
                $script:LastReconnectCause = 'SERIAL_NO_COINCIDE'
                Write-AdbLog -Level ERROR -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $Endpoint -Message ("SERIAL_NO_COINCIDE; etapa={0}; duracionMs={1}" -f $Source,[int]([DateTime]::UtcNow-$started).TotalMilliseconds)
                return $null
            }
            $expectedHash = Get-AdbPhysicalIdHash -PhysicalId $usbId
        }
    }
    if (-not $expectedHash -and -not $BindIdentity) {
        $record | Add-Member -NotePropertyName Provisional -NotePropertyValue $true -Force
        Write-AdbLog -Level WARN -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $record.Serial -Message ("PROVISIONAL: identidad sin confirmar; etapa=$Source; duracionMs=$([int]([DateTime]::UtcNow-$started).TotalMilliseconds)")
        Write-Host ("Conexion provisional: {0} ({1}). Verifica por USB o confirma la vinculacion para guardarla." -f $Device.FriendlyName,$record.Serial) -ForegroundColor Yellow
        return $record
    }
    if (-not $wifiHash) { return $null }
    Add-AdbEndpointHistory -DeviceId $Device.Id -Endpoint $record.Serial -Source $Source -PhysicalIdHash $wifiHash | Out-Null
    Write-AdbLog -Level SUCCESS -Source 'android_reconnect.ps1:Connect-And-RememberDevice' -Serial $record.Serial -Message ("Conexion verificada para {0}; etapa={1}; duracionMs={2}" -f $Device.FriendlyName,$Source,[int]([DateTime]::UtcNow-$started).TotalMilliseconds)
    Write-Host ("OK: {0} verificado en estado device ({1})." -f $Device.FriendlyName,$record.Serial) -ForegroundColor Green
    return $record
}

function Test-EndpointCurrentNetwork {
    param([object]$Entry)
    # Los registros antiguos y las conexiones sin WLAN no tienen BSSID. El
    # candidato sigue pasando por la verificación de identidad física antes
    # de actualizar el historial.
    if ($null -eq $Entry -or $null -eq $Entry.PSObject.Properties['NetworkBssid'] -or -not $Entry.NetworkBssid) { return $true }
    $current = Get-CurrentWifiBssid
    return ($current -and $current -ieq [string]$Entry.NetworkBssid)
}

function Invoke-SmartReconnect {
    [CmdletBinding()]
    param([string]$DeviceId)

    $device = if ($DeviceId) { @(Get-SavedAdbDevices | Where-Object { $_.Id -ceq $DeviceId })[0] } else { Select-SavedDevice }
    if ($null -eq $device) { throw 'No hay un dispositivo guardado para reconectar.' }
    $started = [DateTime]::UtcNow
    $deadline = $started.AddSeconds(30)
    $extended = $false
    $attempt = 0
    $lastCode = 'CONNECT_RECHAZADO'
    $script:LastReconnectCause = $null
    while ($true) {
        $attempt++
        $device = @(Get-SavedAdbDevices | Where-Object { $_.Id -ceq $device.Id })[0]
        if ($device.Mode -eq 'tcpip-5555' -and $device.UsbSerial) {
            $usb = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $device.UsbSerial })[0]
            if ($usb -and $usb.Status -ceq 'device') {
                try {
                    $record = Invoke-AdbUsbWifiConversion -Device $device
                    if ($record) { return $record }
                } catch {
                    $lastCode = ($_.Exception.Message -split ':')[0]
                    Write-AdbLog -Level WARN -Source 'android_reconnect.ps1:Invoke-SmartReconnect' -Serial $device.UsbSerial -Message $_.Exception.Message
                }
            } elseif ($usb -and $usb.Status -ceq 'unauthorized') { $lastCode = 'USB_UNAUTHORIZED' }
            elseif ($usb) { $lastCode = 'OFFLINE' }
            else { $lastCode = 'USB_NO_DETECTADO' }
        }
        $candidates = @()
        if ($device.Mode -eq 'wireless-debugging-tls') {
            $mdns = Select-MdnsEndpoint -Device $device -NonInteractive
            if ($mdns) { $candidates = @($mdns) + $candidates }
        }
        $localCandidates = @()
        $lastEntry = @($device.Endpoints | Where-Object { $_.Endpoint -ieq [string]$device.LastEndpoint })[0]
        if ($device.LastEndpoint -and (Test-EndpointCurrentNetwork $lastEntry)) { $localCandidates += [string]$device.LastEndpoint }
        if ($device.PSObject.Properties['Endpoints']) {
            foreach ($entry in @($device.Endpoints)) {
                $endpoint = [string]$entry.Endpoint
                if ($endpoint -and (Test-EndpointCurrentNetwork $entry)) { $localCandidates += $endpoint }
            }
        }
        $localCandidates = @($localCandidates | Select-Object -Unique)
        if ($device.LastSuccessfulReconnectMethod -eq 'history' -and $localCandidates.Count -gt 1) { [array]::Reverse($localCandidates) }
        $candidates += $localCandidates
        $newUsb = $null
        foreach ($endpoint in @($candidates | Select-Object -Unique)) {
            if ([DateTime]::UtcNow -ge $deadline) { break }
            if ($device.Mode -eq 'tcpip-5555' -and $device.UsbSerial) {
                $newUsb = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $device.UsbSerial -and $_.Status -ceq 'device' })[0]
                if ($newUsb) { break }
            }
            $state = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $endpoint })[0]
            if ($state -and $state.Status -ceq 'offline') {
                Write-AdbLog -Level INFO -Source 'android_reconnect.ps1:Invoke-SmartReconnect' -Serial $endpoint -Message 'OFFLINE: desconexion dirigida al endpoint'
                try { Invoke-AdbHost -Arguments @('disconnect',$endpoint) | Out-Null } catch { }
            }
            $source = if ($mdns -and $endpoint -ceq $mdns) { 'mdns' } else { 'reconnect' }
            $record = Connect-And-RememberDevice -Device $device -Endpoint $endpoint -TimeoutSeconds 4 -Source $source
            if ($record) { return $record }
            if ($script:LastReconnectCause) { $lastCode = $script:LastReconnectCause }
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            if (-not $extended) {
                Write-Host ("Primer intento agotado. Ultima causa: {0}." -f $lastCode) -ForegroundColor Yellow
                if ((Read-Host 'Continuar otros 30 segundos? [s/N]').Trim() -match '^(?i:s|si|s?|y|yes)$') {
                    $deadline = $started.AddSeconds(60)
                    if ([DateTime]::UtcNow -ge $deadline) { $deadline = [DateTime]::UtcNow.AddSeconds(30) }
                    $extended = $true
                    continue
                }
            }
            break
        }
        if ($newUsb) { continue }
        Start-Sleep -Seconds ([Math]::Min(4,[Math]::Pow(2,[Math]::Min($attempt-1,2))))
    }
    Write-AdbLog -Level ERROR -Source 'android_reconnect.ps1:Invoke-SmartReconnect' -Serial $device.UsbSerial -Message ("Reconexion agotada; causa={0}" -f $lastCode)
    Write-Host ("Reconexion automatica agotada. Diagnostico: {0}." -f $lastCode) -ForegroundColor Yellow
    $foreign = @($device.Endpoints | Where-Object { $_.Endpoint -and -not (Test-EndpointCurrentNetwork $_) })
    if ($device.LastEndpoint -and -not (Test-EndpointCurrentNetwork $lastEntry) -and @($foreign | Where-Object { $_.Endpoint -ieq $device.LastEndpoint }).Count -eq 0) {
        $foreign += [pscustomobject]@{Endpoint=$device.LastEndpoint}
    }
    if ($foreign.Count -gt 0 -and (Read-Host 'Probar endpoints historicos de otra red? [s/N]').Trim() -match '^(?i:s|si|s?|y|yes)$') {
        foreach ($entry in $foreign) {
            $record = Connect-And-RememberDevice -Device $device -Endpoint ([string]$entry.Endpoint)
            if ($record) { return $record }
        }
    }
    $manualEndpoint = Read-ManualEndpoint -Mode $device.Mode
    $bind = (Read-Host 'Confirmar que este endpoint pertenece al telefono y guardar su identidad? [s/N]').Trim() -match '^(?i:s|si|y|yes)$'
    $record = Connect-And-RememberDevice -Device $device -Endpoint $manualEndpoint -Source 'manual' -BindIdentity:$bind
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
    $transport = ((@(Invoke-Adb -Serial $record.Serial -Arguments @('get-serialno')) | ForEach-Object { [string]$_ }) -join '').Trim()
    $physicalId = Get-AdbPhysicalId -Serial $record.Serial
    if ($transport -cne $record.Serial -or -not $physicalId) { throw 'SERIAL_NO_COINCIDE: no se verifico el endpoint emparejado.' }
    $physicalHash = Get-AdbPhysicalIdHash -PhysicalId $physicalId
    $device = Add-SavedAdbDevice -FriendlyName $name -Mode 'wireless-debugging-tls' -LastEndpoint $record.Serial -IsPaired $true -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
    try {
        $matchedService = @(Get-AdbMdnsServices | Where-Object { $_.Endpoint -ieq $record.Serial })[0]
        if ($matchedService) {
            $store = Read-AdbDeviceStore
            $saved = @($store.Devices | Where-Object { $_.Id -ceq $device.Id })[0]
            $saved | Add-Member -NotePropertyName MdnsServiceName -NotePropertyValue $matchedService.ServiceName -Force
            Save-AdbDeviceStore -Store $store | Out-Null
        }
    } catch { Write-AdbLog -Level WARN -Source 'android_reconnect.ps1:Add-WirelessDevice' -Message $_.Exception.Message }
    Add-AdbEndpointHistory -DeviceId $device.Id -Endpoint $record.Serial -Source 'mdns' -PhysicalIdHash $physicalHash | Out-Null
    Write-Host ("Dispositivo guardado: {0} [{1}]" -f $device.FriendlyName,$device.Id) -ForegroundColor Green
}

function Add-LegacyUsbDevice {
    $usb = Select-ReadyUsbDevice
    $name = Read-RequiredText 'Nombre amigable'
    $device = Add-SavedAdbDevice -FriendlyName $name -Mode 'tcpip-5555' -UsbSerial $usb.Serial -IsPaired $false
    $record = Invoke-AdbUsbWifiConversion -Device $device
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
        } catch { Write-AdbLog -Level ERROR -Source 'android_reconnect.ps1:Start-AdbDeviceManager' -Message $_.Exception.Message; Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red }
    }
}

if (-not $NoMenu) { Start-AdbDeviceManager }
Write-AdbLog -Level INFO -Source 'android_reconnect.ps1' -Message 'Fin de administrador/reconexion.'
