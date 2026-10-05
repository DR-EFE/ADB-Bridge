# Funciones comunes para la configuracion ADB de este proyecto.
# Compatible con Windows PowerShell 5.1.

function Get-AndroidConfigPath {
    $configDirectory = Join-Path -Path $env:USERPROFILE -ChildPath 'AndroidTools'
    return (Join-Path -Path $configDirectory -ChildPath 'android_config.json')
}

function ConvertTo-AndroidEndpointParts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint
    )

    $value = $Endpoint.Trim()
    $match = [regex]::Match($value, '^\[(?<host>[^\]]+)\]:(?<port>\d{1,5})$')
    if (-not $match.Success) {
        $match = [regex]::Match($value, '^(?<host>[^:]+):(?<port>\d{1,5})$')
    }

    if (-not $match.Success) {
        throw "Endpoint invalido. Se esperaba host:puerto: $Endpoint"
    }

    $port = 0
    if (-not [int]::TryParse($match.Groups['port'].Value, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$port)) {
        throw "Puerto invalido en el endpoint: $Endpoint"
    }

    if ($port -lt 1 -or $port -gt 65535) {
        throw "El puerto debe estar entre 1 y 65535: $Endpoint"
    }

    $hostName = $match.Groups['host'].Value
    $normalizedEndpoint = if ($hostName.Contains(':')) {
        "[{0}]:{1}" -f $hostName, $port
    } else {
        "{0}:{1}" -f $hostName, $port
    }

    return [pscustomobject]@{
        Host     = $hostName
        Port     = $port
        Endpoint = $normalizedEndpoint
    }
}

function Save-AndroidConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,

        [Parameter(Mandatory = $true)]
        [int]$Port,

        [Parameter(Mandatory = $true)]
        [string]$Endpoint,

        [Parameter(Mandatory = $true)]
        [ValidateSet('ModernPairing', 'LegacyTcpIp')]
        [string]$Mode,

        [Parameter(Mandatory = $true)]
        [string]$Serial
    )

    $parts = ConvertTo-AndroidEndpointParts -Endpoint $Endpoint
    if ($parts.Host -ne $HostName -or $parts.Port -ne $Port) {
        throw 'Host, Port y Endpoint no coinciden en la configuracion ADB.'
    }

    $configPath = Get-AndroidConfigPath
    $configDirectory = Split-Path -Parent $configPath
    if (-not (Test-Path -LiteralPath $configDirectory)) {
        New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
    }

    $configuration = [ordered]@{
        Host       = $HostName
        Port       = $Port
        Endpoint   = $parts.Endpoint
        Mode       = $Mode
        Serial     = $Serial
        UpdatedUtc = [DateTime]::UtcNow.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
    }

    $json = $configuration | ConvertTo-Json -Depth 3
    $utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    [IO.File]::WriteAllText($configPath, $json, $utf8NoBom)
    return $configPath
}

function Read-AndroidConfig {
    $configPath = Get-AndroidConfigPath
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "No existe la configuracion JSON: $configPath"
    }

    try {
        $configuration = Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop | ConvertFrom-Json
    } catch {
        throw "No se pudo leer la configuracion JSON '$configPath': $($_.Exception.Message)"
    }

    foreach ($propertyName in @('Host', 'Port', 'Endpoint', 'Mode', 'Serial', 'UpdatedUtc')) {
        if ($null -eq $configuration.$propertyName -or [string]::IsNullOrWhiteSpace([string]$configuration.$propertyName)) {
            throw "La configuracion JSON no contiene el campo requerido '$propertyName'."
        }
    }

    $parts = ConvertTo-AndroidEndpointParts -Endpoint ([string]$configuration.Endpoint)
    $configuration | Add-Member -NotePropertyName ConfigPath -NotePropertyValue $configPath -Force
    $configuration | Add-Member -NotePropertyName ParsedEndpoint -NotePropertyValue $parts -Force
    return $configuration
}

function Get-AndroidDeviceRecords {
    $output = @(& adb devices 2>&1)
    $adbExitCode = $LASTEXITCODE
    if ($adbExitCode -ne 0) {
        throw "adb devices fallo con codigo $adbExitCode. Salida: $($output -join ' ')"
    }

    foreach ($line in $output) {
        $text = [string]$line
        $match = [regex]::Match($text, '^\s*(?<serial>\S+)\s+(?<status>\S+)(?:\s+.*)?$')
        if ($match.Success -and $match.Groups['serial'].Value -ne 'List') {
            [pscustomobject]@{
                Serial = $match.Groups['serial'].Value
                Status = $match.Groups['status'].Value
                Raw    = $text.Trim()
            }
        }
    }
}

function Get-AndroidDeviceRecord {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Serial
    )

    return @(Get-AndroidDeviceRecords | Where-Object { $_.Serial -ceq $Serial })[0]
}

function Test-AndroidDeviceReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Serial
    )

    $record = Get-AndroidDeviceRecord -Serial $Serial
    return ($null -ne $record -and $record.Status -ceq 'device')
}

function Get-AndroidMdnsConnectionEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PreferredHost
    )

    $lines = @(& adb mdns services 2>&1)
    $candidates = @()

    foreach ($line in $lines) {
        $text = [string]$line
        if ($text -notmatch 'adb-tls-connect') {
            continue
        }

        $match = [regex]::Match($text, '(?<endpoint>\[[^\]]+\]:\d{1,5}|[A-Za-z0-9._-]+:\d{1,5})')
        if (-not $match.Success) {
            continue
        }

        try {
            $candidate = ConvertTo-AndroidEndpointParts -Endpoint $match.Groups['endpoint'].Value
        } catch {
            continue
        }

        if ($candidate.Host -ieq $PreferredHost) {
            return $candidate
        }

        $candidates += $candidate
    }

    if ($candidates.Count -eq 1) {
        return $candidates[0]
    }

    return $null
}
