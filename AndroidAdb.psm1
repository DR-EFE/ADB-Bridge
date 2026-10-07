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
    Invoke-AdbHost -Arguments @('connect', $parts.Endpoint) | Out-Null
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

Export-ModuleMember -Function Get-AdbDevices, Get-SingleReadyDevice, Invoke-Adb, Invoke-AdbHost, Test-AdbAvailable, ConvertTo-AdbEndpoint, Get-AndroidToolsConfigPath, Save-AdbConnectionConfig, Read-AdbConnectionConfig, Get-AdbDeviceRecord, Wait-AdbDevice, Connect-AdbEndpoint, Get-AdbMdnsConnectionEndpoint, ConvertTo-SafeAdbSearchPattern, ConvertTo-SafeAndroidPath, ConvertTo-SafeWindowsName, Protect-BackupDirectory
