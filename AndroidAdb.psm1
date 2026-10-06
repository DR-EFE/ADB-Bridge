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

Export-ModuleMember -Function Get-AdbDevices, Get-SingleReadyDevice, Invoke-Adb
