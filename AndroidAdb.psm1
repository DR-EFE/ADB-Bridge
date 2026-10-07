Set-StrictMode -Version 2.0

function Get-AdbDevices {
    [CmdletBinding()]
    param()

    $output = @(& adb devices 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "adb devices fallo con codigo $exitCode. Salida: $($output -join ' ')"
    }

    foreach ($line in $output) {
        $text = [string]$line
        $match = [regex]::Match($text, '^\s*(?<serial>\S+)\s+(?<status>\S+)(?:\s+.*)?$')
        if ($match.Success -and $match.Groups['serial'].Value -cne 'List') {
            [pscustomobject]@{
                Serial = $match.Groups['serial'].Value
                Status = $match.Groups['status'].Value
                Raw    = $text.Trim()
            }
        }
    }
}

function Get-SingleReadyDevice {
    [CmdletBinding()]
    param()

    $ready = @(Get-AdbDevices | Where-Object { $_.Status -ceq 'device' })
    if ($ready.Count -eq 0) {
        throw 'No hay dispositivos ADB en estado exacto device.'
    }
    if ($ready.Count -gt 1) {
        $serials = $ready.Serial -join ', '
        throw "Hay mas de un dispositivo ADB listo ($($ready.Count)): $serials. Seleccion silenciosa cancelada."
    }
    return $ready[0]
}

function Invoke-Adb {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Serial,

        [Parameter(Mandatory = $true, Position = 0)]
        [ValidateNotNull()]
        [string[]]$Arguments
    )

    $adbArguments = @('-s', $Serial) + $Arguments
    $output = @(& adb @adbArguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $detail = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
        throw "adb -s '$Serial' fallo con codigo $exitCode. $detail"
    }
    return $output
}

function ConvertTo-SafeAdbSearchPattern {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Value)

    $value = $Value.Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 64) {
        throw 'La busqueda debe tener entre 1 y 64 caracteres.'
    }
    if ($value -notmatch '^[A-Za-z0-9._* -]+$') {
        throw 'La busqueda solo permite letras, numeros, espacio, punto, guion, guion bajo y asterisco.'
    }
    return $value
}

function ConvertTo-SafeAndroidPath {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $value = $Path.Trim().TrimEnd('/')
    if ($value -notmatch '^/(sdcard|storage|data|system|cache)(/[A-Za-z0-9._ -]+)*$' -or $value -match '(^|/)\.\.(/|$)') {
        throw "Ruta Android no permitida: $Path"
    }
    return $value
}

function ConvertTo-SafeWindowsName {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = $Name.Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 80) {
        throw 'El nombre debe tener entre 1 y 80 caracteres.'
    }
    if ($value -notmatch '^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$' -or $value -in @('.', '..') -or $value -match '[\. ]$') {
        throw "Nombre de archivo/carpeta no permitido: $Name"
    }
    if ($value -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$') {
        throw "Nombre reservado de Windows: $Name"
    }
    return $value
}

function Protect-BackupDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        if (-not (Test-Path -LiteralPath $Path)) {
            New-Item -ItemType Directory -Path $Path -Force | Out-Null
        }
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        $propagate = [Security.AccessControl.PropagationFlags]::None
        $allow = [Security.AccessControl.AccessControlType]::Allow
        foreach ($identity in @("$env:USERDOMAIN\$env:USERNAME", 'SYSTEM')) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inherit, $propagate, $allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
        return $true
    } catch {
        Write-Warning "No se pudo proteger ACL de '$Path'. Los respaldos pueden quedar accesibles para otros usuarios: $($_.Exception.Message)"
        return $false
    }
}

function Test-AdbAvailable {
    [CmdletBinding()]
    param()

    if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
        throw 'adb no se encuentra en PATH. Instala Android Platform Tools o agrega su carpeta a PATH.'
    }
    return $true
}

function Invoke-AdbHost {
    <#
    .SYNOPSIS
    Ejecuta una orden ADB que no puede llevar -s, por ejemplo pair, connect o mdns.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    Test-AdbAvailable | Out-Null
    $output = @(& adb @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $detail = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
        throw "adb $($Arguments -join ' ') fallo con codigo $exitCode. $detail"
    }
    return $output
}

function ConvertTo-AdbEndpoint {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Endpoint)

    $value = $Endpoint.Trim()
    $match = [regex]::Match($value, '^\[(?<host>[^\]]+)\]:(?<port>\d{1,5})$')
    if (-not $match.Success) {
        $match = [regex]::Match($value, '^(?<host>[^:\s]+):(?<port>\d{1,5})$')
    }
    if (-not $match.Success) { throw "Endpoint ADB invalido: $Endpoint" }

    $port = [int]$match.Groups['port'].Value
    if ($port -lt 1 -or $port -gt 65535) { throw "Puerto ADB invalido: $Endpoint" }
    $host = $match.Groups['host'].Value
    if ([string]::IsNullOrWhiteSpace($host) -or $host -match '\s') { throw "Host ADB invalido: $Endpoint" }
    $normalized = if ($host.Contains(':')) { "[{0}]:{1}" -f $host,$port } else { "{0}:{1}" -f $host,$port }
    return [pscustomobject]@{ Host=$host; Port=$port; Endpoint=$normalized }
}

function Get-AndroidToolsConfigPath {
    [CmdletBinding()]
    param()
    return (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'android_config.json')
}

function Save-AdbConnectionConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet('WirelessTls','LegacyTcpIp')][string]$Mode,
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$Serial
    )

    $parts = ConvertTo-AdbEndpoint $Endpoint
    $path = Get-AndroidToolsConfigPath
    $directory = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $config = [ordered]@{
        SchemaVersion = 1
        Mode          = $Mode
        Host          = $parts.Host
        Port          = $parts.Port
        Endpoint      = $parts.Endpoint
        Serial        = $Serial
        UpdatedUtc    = [DateTime]::UtcNow.ToString('o')
    }
    $json = $config | ConvertTo-Json -Depth 3
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding($false)))
    return $path
}

function Read-AdbConnectionConfig {
    [CmdletBinding()]
    param()

    $path = Get-AndroidToolsConfigPath
    if (-not (Test-Path -LiteralPath $path)) { throw "No existe la configuracion ADB: $path" }
    try { $config = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { throw "No se pudo leer '$path': $($_.Exception.Message)" }

    foreach ($name in @('SchemaVersion','Mode','Endpoint','Serial','UpdatedUtc')) {
        if ($null -eq $config.$name -or [string]::IsNullOrWhiteSpace([string]$config.$name)) {
            throw "La configuracion ADB no contiene '$name'."
        }
    }
    # ModernPairing fue el nombre usado por versiones anteriores del proyecto.
    if ([string]$config.Mode -eq 'ModernPairing') { $config.Mode = 'WirelessTls' }
    if ([string]$config.Mode -notin @('WirelessTls','LegacyTcpIp')) { throw "Modo de conexion no admitido: $($config.Mode)" }
    $parts = ConvertTo-AdbEndpoint ([string]$config.Endpoint)
    $config | Add-Member -NotePropertyName EndpointParts -NotePropertyValue $parts -Force
    $config | Add-Member -NotePropertyName ConfigPath -NotePropertyValue $path -Force
    return $config
}

function Get-AdbDeviceRecord {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Serial)
    return @(Get-AdbDevices | Where-Object { $_.Serial -ceq $Serial })[0]
}

function Wait-AdbDevice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [ValidateRange(1,120)][int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $record = Get-AdbDeviceRecord -Serial $Serial
        if ($null -ne $record -and $record.Status -ceq 'device') { return $record }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    $last = Get-AdbDeviceRecord -Serial $Serial
    $state = if ($null -eq $last) { 'no encontrado' } else { $last.Status }
    throw "El serial '$Serial' no alcanzo el estado exacto device. Estado final: $state"
}

function Connect-AdbEndpoint {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [ValidateRange(1,120)][int]$TimeoutSeconds = 15
    )

    $parts = ConvertTo-AdbEndpoint $Endpoint
    $connectOutput = @(Invoke-AdbHost -Arguments @('connect', $parts.Endpoint))
    $connectText = ($connectOutput | ForEach-Object { [string]$_ }) -join ' '
    if ($connectText -match '(?i)cannot connect|failed to connect|unable to connect|connection refused|timed out|no route|10013') {
        throw "adb connect no pudo alcanzar $($parts.Endpoint). Salida: $connectText"
    }
    return Wait-AdbDevice -Serial $parts.Endpoint -TimeoutSeconds $TimeoutSeconds
}

function Get-AdbMdnsConnectionEndpoint {
    [CmdletBinding()]
    param([string]$PreferredHost)

    $lines = @(Invoke-AdbHost -Arguments @('mdns','services'))
    $candidates = @()
    foreach ($line in $lines) {
        $text = [string]$line
        if ($text -notmatch 'adb-tls-connect') { continue }
        $match = [regex]::Match($text, '(?<endpoint>\[[^\]]+\]:\d{1,5}|[A-Za-z0-9._-]+:\d{1,5})')
        if (-not $match.Success) { continue }
        try { $candidate = ConvertTo-AdbEndpoint $match.Groups['endpoint'].Value } catch { continue }
        if (-not [string]::IsNullOrWhiteSpace($PreferredHost) -and $candidate.Host -ieq $PreferredHost) { return $candidate }
        $candidates += $candidate
    }
    if ($candidates.Count -eq 1) { return $candidates[0] }
    return $null
}

function Get-AdbDeviceStorePath {
    [CmdletBinding()]
    param()
    return (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'devices.json')
}

function New-AdbDeviceStore {
    [CmdletBinding()]
    param()
    return [pscustomobject][ordered]@{
        SchemaVersion = 1
        UpdatedUtc    = [DateTime]::UtcNow.ToString('o')
        Devices       = @()
    }
}

function Assert-AdbFriendlyName {
    param([string]$FriendlyName)
    $value = if ($null -eq $FriendlyName) { '' } else { $FriendlyName.Trim() }
    if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 80 -or $value -match '[\x00-\x1F]') {
        throw 'El nombre amigable debe tener entre 1 y 80 caracteres y no contener caracteres de control.'
    }
    return $value
}

function ConvertTo-AdbUtcTimestamp {
    param([AllowNull()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $parsed = [DateTimeOffset]::MinValue
    $valid = [DateTimeOffset]::TryParse(
        $Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    )
    if (-not $valid) { throw "Fecha UTC invalida en devices.json: $Value" }
    return $parsed.UtcDateTime.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
}

function ConvertTo-AdbBoolean {
    param([object]$Value,[string]$FieldName)
    if ($Value -is [bool]) { return [bool]$Value }
    $parsed = $false
    if ($null -ne $Value -and [bool]::TryParse([string]$Value,[ref]$parsed)) { return $parsed }
    throw "El campo '$FieldName' debe ser true o false."
}

function ConvertTo-AdbStoredMode {
    param([string]$Mode)
    switch ($Mode) {
        'wireless-debugging-tls' { return 'wireless-debugging-tls' }
        'WirelessTls'            { return 'wireless-debugging-tls' }
        'ModernPairing'          { return 'wireless-debugging-tls' }
        'tcpip-5555'             { return 'tcpip-5555' }
        'LegacyTcpIp'            { return 'tcpip-5555' }
        default { throw "Modo de dispositivo no admitido: $Mode" }
    }
}

function Save-AdbDeviceStore {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][object]$Store)

    $path = Get-AdbDeviceStorePath
    $directory = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if ($null -eq $Store -or $null -eq $Store.PSObject.Properties['Devices']) {
        throw 'El registro de dispositivos no contiene la propiedad Devices.'
    }
    $devices = @($Store.Devices)
    $document = [ordered]@{
        SchemaVersion = 1
        UpdatedUtc    = [DateTime]::UtcNow.ToString('o')
        Devices       = $devices
    }
    $json = $document | ConvertTo-Json -Depth 8
    $temporaryPath = "{0}.{1}.tmp" -f $path,[guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temporaryPath, $json, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporaryPath -Destination $path -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue }
    }
    return $path
}

function Import-LegacyAdbDeviceConfig {
    param()
    $legacyPath = Get-AndroidToolsConfigPath
    if (-not (Test-Path -LiteralPath $legacyPath)) { return $null }
    try { $legacy = Get-Content -LiteralPath $legacyPath -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { return $null }
    if ([string]::IsNullOrWhiteSpace([string]$legacy.Endpoint)) { return $null }
    try { $endpoint = (ConvertTo-AdbEndpoint ([string]$legacy.Endpoint)).Endpoint }
    catch { return $null }
    try { $mode = ConvertTo-AdbStoredMode ([string]$legacy.Mode) }
    catch { return $null }
    try {
        $lastConnectedUtc = if ($legacy.UpdatedUtc) { ConvertTo-AdbUtcTimestamp ([string]$legacy.UpdatedUtc) } else { $null }
    } catch { $lastConnectedUtc = $null }
    $hostName = (ConvertTo-AdbEndpoint $endpoint).Host
    return [pscustomobject][ordered]@{
        Id               = [guid]::NewGuid().ToString('N')
        FriendlyName     = "Android $hostName"
        UsbSerial        = $null
        LastEndpoint     = $endpoint
        Mode             = $mode
        LastConnectedUtc = $lastConnectedUtc
        IsPaired         = ($mode -eq 'wireless-debugging-tls')
    }
}

function Read-AdbDeviceStore {
    [CmdletBinding()]
    param()

    $path = Get-AdbDeviceStorePath
    if (-not (Test-Path -LiteralPath $path)) {
        $store = New-AdbDeviceStore
        $legacyDevice = Import-LegacyAdbDeviceConfig
        if ($null -ne $legacyDevice) {
            $store.Devices = @($legacyDevice)
        }
        Save-AdbDeviceStore -Store $store | Out-Null
        return $store
    }
    try { $store = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { throw "No se pudo leer '$path': $($_.Exception.Message)" }
    if ($null -eq $store -or $null -eq $store.PSObject.Properties['SchemaVersion']) {
        throw 'devices.json no contiene SchemaVersion.'
    }
    if ([int]$store.SchemaVersion -ne 1) { throw "Version de devices.json no admitida: $($store.SchemaVersion)" }
    if ($null -eq $store.PSObject.Properties['Devices']) { throw 'devices.json no contiene Devices.' }
    $updatedUtc = if ($null -eq $store.PSObject.Properties['UpdatedUtc']) { $null } else { ConvertTo-AdbUtcTimestamp ([string]$store.UpdatedUtc) }
    $normalized = @()
    $seenIds = @{}
    foreach ($device in @($store.Devices)) {
        foreach ($field in @('Id','FriendlyName','UsbSerial','LastEndpoint','Mode','LastConnectedUtc','IsPaired')) {
            if ($null -eq $device.PSObject.Properties[$field]) {
                throw "devices.json contiene un dispositivo sin el campo '$field'."
            }
        }
        $id = [string]$device.Id
        if ([string]::IsNullOrWhiteSpace($id) -or [string]::IsNullOrWhiteSpace([string]$device.FriendlyName)) {
            throw 'devices.json contiene un dispositivo sin Id o FriendlyName.'
        }
        if ($seenIds.ContainsKey($id)) { throw "devices.json contiene el Id duplicado '$id'." }
        $seenIds[$id] = $true
        $mode = ConvertTo-AdbStoredMode ([string]$device.Mode)
        $endpoint = $null
        if (-not [string]::IsNullOrWhiteSpace([string]$device.LastEndpoint)) {
            $endpoint = (ConvertTo-AdbEndpoint ([string]$device.LastEndpoint)).Endpoint
        }
        $normalized += [pscustomobject][ordered]@{
            Id               = $id
            FriendlyName     = Assert-AdbFriendlyName ([string]$device.FriendlyName)
            UsbSerial        = if ([string]::IsNullOrWhiteSpace([string]$device.UsbSerial)) { $null } else { [string]$device.UsbSerial }
            LastEndpoint     = $endpoint
            Mode             = $mode
            LastConnectedUtc = ConvertTo-AdbUtcTimestamp ([string]$device.LastConnectedUtc)
            IsPaired         = ConvertTo-AdbBoolean $device.IsPaired 'IsPaired'
        }
    }
    return [pscustomobject][ordered]@{ SchemaVersion=1; UpdatedUtc=$updatedUtc; Devices=@($normalized) }
}

function Get-SavedAdbDevices {
    [CmdletBinding()]
    param()
    return @((Read-AdbDeviceStore).Devices)
}

function Add-SavedAdbDevice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FriendlyName,
        [Parameter(Mandatory = $true)][string]$Mode,
        [string]$UsbSerial,
        [string]$LastEndpoint,
        [bool]$IsPaired = $false,
        [string]$LastConnectedUtc
    )

    $store = Read-AdbDeviceStore
    $name = Assert-AdbFriendlyName $FriendlyName
    $storedMode = ConvertTo-AdbStoredMode $Mode
    $endpoint = if ([string]::IsNullOrWhiteSpace($LastEndpoint)) { $null } else { (ConvertTo-AdbEndpoint $LastEndpoint).Endpoint }
    $usb = if ([string]::IsNullOrWhiteSpace($UsbSerial)) { $null } else { $UsbSerial.Trim() }
    $duplicate = @($store.Devices | Where-Object {
        ($usb -and $_.UsbSerial -ceq $usb) -or ($endpoint -and $_.LastEndpoint -ieq $endpoint)
    })[0]
    if ($null -ne $duplicate) { throw "El dispositivo ya existe: $($duplicate.FriendlyName) [$($duplicate.Id)]" }
    $device = [pscustomobject][ordered]@{
        Id               = [guid]::NewGuid().ToString('N')
        FriendlyName     = $name
        UsbSerial        = $usb
        LastEndpoint     = $endpoint
        Mode             = $storedMode
        LastConnectedUtc = ConvertTo-AdbUtcTimestamp $LastConnectedUtc
        IsPaired         = $IsPaired
    }
    $store.Devices = @($store.Devices) + $device
    Save-AdbDeviceStore -Store $store | Out-Null
    return $device
}

function Update-SavedAdbDevice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [string]$FriendlyName,
        [string]$UsbSerial,
        [string]$LastEndpoint,
        [string]$Mode,
        [Nullable[bool]]$IsPaired,
        [string]$LastConnectedUtc
    )

    $store = Read-AdbDeviceStore
    $device = @($store.Devices | Where-Object { $_.Id -ceq $Id })[0]
    if ($null -eq $device) { throw "No existe un dispositivo con Id '$Id'." }
    if ($PSBoundParameters.ContainsKey('FriendlyName')) { $device.FriendlyName = Assert-AdbFriendlyName $FriendlyName }
    if ($PSBoundParameters.ContainsKey('UsbSerial')) { $device.UsbSerial = if ([string]::IsNullOrWhiteSpace($UsbSerial)) { $null } else { $UsbSerial.Trim() } }
    if ($PSBoundParameters.ContainsKey('LastEndpoint')) { $device.LastEndpoint = if ([string]::IsNullOrWhiteSpace($LastEndpoint)) { $null } else { (ConvertTo-AdbEndpoint $LastEndpoint).Endpoint } }
    if ($PSBoundParameters.ContainsKey('Mode')) { $device.Mode = ConvertTo-AdbStoredMode $Mode }
    if ($PSBoundParameters.ContainsKey('IsPaired')) { $device.IsPaired = [bool]$IsPaired }
    if ($PSBoundParameters.ContainsKey('LastConnectedUtc')) { $device.LastConnectedUtc = ConvertTo-AdbUtcTimestamp $LastConnectedUtc }
    $conflict = @($store.Devices | Where-Object {
        $_.Id -cne $device.Id -and (
            ($device.UsbSerial -and $_.UsbSerial -ceq $device.UsbSerial) -or
            ($device.LastEndpoint -and $_.LastEndpoint -ieq $device.LastEndpoint)
        )
    })[0]
    if ($null -ne $conflict) { throw "La actualizacion coincide con '$($conflict.FriendlyName)' [$($conflict.Id)]." }
    Save-AdbDeviceStore -Store $store | Out-Null
    return $device
}

function Remove-SavedAdbDevice {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Id)
    $store = Read-AdbDeviceStore
    $before = @($store.Devices).Count
    $store.Devices = @($store.Devices | Where-Object { $_.Id -cne $Id })
    if (@($store.Devices).Count -eq $before) { throw "No existe un dispositivo con Id '$Id'." }
    Save-AdbDeviceStore -Store $store | Out-Null
    return $true
}

function Get-AdbMdnsServices {
    [CmdletBinding()]
    param()
    $services = @()
    foreach ($line in @(Invoke-AdbHost -Arguments @('mdns','services'))) {
        $text = [string]$line
        if ($text -notmatch 'adb-tls-connect') { continue }
        $endpointMatch = [regex]::Match($text, '(?<endpoint>\[[^\]]+\]:\d{1,5}|[A-Za-z0-9._-]+:\d{1,5})')
        if (-not $endpointMatch.Success) { continue }
        try { $parts = ConvertTo-AdbEndpoint $endpointMatch.Groups['endpoint'].Value } catch { continue }
        $tokens = $text.Trim() -split '\s+'
        $services += [pscustomobject]@{
            ServiceName = if ($tokens.Count -gt 0) { $tokens[0] } else { $null }
            Type        = 'adb-tls-connect'
            Host        = $parts.Host
            Port        = $parts.Port
            Endpoint    = $parts.Endpoint
            Raw         = $text.Trim()
        }
    }
    return @($services)
}

function Get-AdbDeviceIpv4 {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Serial)
    foreach ($arguments in @(@('shell','ip','route'), @('shell','ip','addr'))) {
        foreach ($line in @(Invoke-Adb -Serial $Serial -Arguments $arguments)) {
            $match = [regex]::Match([string]$line, '(?:\bsrc\s+|\binet\s+)(?<ip>\d{1,3}(?:\.\d{1,3}){3})(?:/\d+)?')
            if ($match.Success -and $match.Groups['ip'].Value -notlike '127.*') { return $match.Groups['ip'].Value }
        }
    }
    return $null
}

Export-ModuleMember -Function Get-AdbDevices, Get-SingleReadyDevice, Invoke-Adb, Invoke-AdbHost, Test-AdbAvailable, ConvertTo-AdbEndpoint, Get-AndroidToolsConfigPath, Save-AdbConnectionConfig, Read-AdbConnectionConfig, Get-AdbDeviceRecord, Wait-AdbDevice, Connect-AdbEndpoint, Get-AdbMdnsConnectionEndpoint, Get-AdbMdnsServices, Get-AdbDeviceIpv4, Get-AdbDeviceStorePath, New-AdbDeviceStore, Read-AdbDeviceStore, Save-AdbDeviceStore, Get-SavedAdbDevices, Add-SavedAdbDevice, Update-SavedAdbDevice, Remove-SavedAdbDevice, ConvertTo-SafeAdbSearchPattern, ConvertTo-SafeAndroidPath, ConvertTo-SafeWindowsName, Protect-BackupDirectory
