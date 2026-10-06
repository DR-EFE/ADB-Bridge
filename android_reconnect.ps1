# Reconecta usando exactamente el endpoint guardado en android_config.json.
# Compatible con Windows PowerShell 5.1.

. (Join-Path -Path $PSScriptRoot -ChildPath 'android_config.ps1')
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force

Write-Host '========================================' -ForegroundColor Cyan
Write-Host '   RECONEXION RAPIDA ANDROID' -ForegroundColor Yellow
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''

try {
    $configuration = Read-AndroidConfig
} catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Ejecuta SETUP.bat para crear la configuracion.' -ForegroundColor Yellow
    Read-Host 'Presiona Enter para salir'
    exit 1
}

$endpoint = [string]$configuration.Endpoint
Write-Host ("Endpoint guardado: {0}" -f $endpoint) -ForegroundColor White
Write-Host ("Modo: {0} | Serial guardado: {1}" -f $configuration.Mode, $configuration.Serial) -ForegroundColor Gray
Write-Host ''

$connected = $false
$maxAttempts = 3
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Write-Host ("Intento {0} de {1}..." -f $attempt, $maxAttempts) -ForegroundColor Gray
    $connectOutput = @(& adb connect $endpoint 2>&1)
    if ($connectOutput.Count -gt 0) {
        Write-Host ($connectOutput -join [Environment]::NewLine) -ForegroundColor DarkGray
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("adb connect fallo con codigo {0}." -f $LASTEXITCODE) -ForegroundColor Yellow
        continue
    }

    Start-Sleep -Seconds 2
    $record = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $endpoint })[0]
    if ($null -ne $record -and $record.Status -ceq 'device') {
        $connected = $true
        break
    }

    if ($null -ne $record) {
        Write-Host ("Estado actual: {0}. Se requiere exactamente 'device'." -f $record.Status) -ForegroundColor Yellow
    } else {
        Write-Host 'El endpoint aun no aparece en adb devices.' -ForegroundColor Yellow
    }

    if ($attempt -lt $maxAttempts) {
        Start-Sleep -Seconds 2
    }
}

if ($connected) {
    Write-Host ''
    Write-Host 'OK: dispositivo conectado en estado exacto device.' -ForegroundColor Green
    Write-Host ("Serial activo: {0}" -f $record.Serial) -ForegroundColor Gray
    Read-Host 'Presiona Enter para salir'
    exit 0
}

Write-Host ''
Write-Host ("ERROR: no se pudo conectar a {0} en estado device." -f $endpoint) -ForegroundColor Red
Write-Host 'Verifica que el telefono este encendido, en la misma red y que Wireless Debugging este activo.' -ForegroundColor Yellow
Write-Host ''
Write-Host 'Estado de ADB:' -ForegroundColor Cyan
Get-AdbDevices | Format-Table -AutoSize
Read-Host 'Presiona Enter para salir'
exit 1
