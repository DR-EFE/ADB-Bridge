# Ayuda para autorizar el endpoint ADB guardado en android_config.json.
# El archivo usa solo texto ASCII para ser seguro en Windows PowerShell 5.1.

. (Join-Path -Path $PSScriptRoot -ChildPath 'android_config.ps1')
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force

Write-Host '=== SOLUCIONANDO ESTADO UNAUTHORIZED ===' -ForegroundColor Yellow
Write-Host ''

try {
    $configuration = Read-AndroidConfig
} catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Ejecuta SETUP.bat primero para crear android_config.json.' -ForegroundColor Yellow
    Read-Host 'Presiona Enter para salir'
    exit 1
}

$endpoint = [string]$configuration.Endpoint
Write-Host ("Endpoint de configuracion: {0}" -f $endpoint) -ForegroundColor White
Write-Host ''

Write-Host '1. Reiniciando ADB...' -ForegroundColor Cyan
adb kill-server | Out-Null
Start-Sleep -Seconds 2
adb start-server | Out-Null
Start-Sleep -Seconds 2

Write-Host '2. Conectando al endpoint configurado...' -ForegroundColor Cyan
$connectOutput = @(& adb connect $endpoint 2>&1)
Write-Host ($connectOutput -join [Environment]::NewLine) -ForegroundColor Gray
Start-Sleep -Seconds 2

Write-Host '3. Estado actual de ADB:' -ForegroundColor Cyan
Get-AdbDevices | Format-Table -AutoSize
$record = Get-AndroidDeviceRecord -Serial $endpoint

if ($null -ne $record -and $record.Status -ceq 'device') {
    Write-Host 'La conexion ya esta autorizada y en estado device.' -ForegroundColor Green
    Read-Host 'Presiona Enter para salir'
    exit 0
}

Write-Host ''
Write-Host 'Si el estado es unauthorized, acepta en el telefono:' -ForegroundColor Yellow
Write-Host '  - Permitir depuracion USB o depuracion inalambrica.' -ForegroundColor White
Write-Host '  - Marca Siempre permitir desde este equipo si aparece.' -ForegroundColor White
Write-Host ''
Read-Host 'Despues de autorizar, presiona Enter aqui'

Write-Host '4. Verificando autorizacion...' -ForegroundColor Cyan
& adb connect $endpoint | Out-Host
Start-Sleep -Seconds 2
$record = Get-AndroidDeviceRecord -Serial $endpoint

if ($null -ne $record -and $record.Status -ceq 'device') {
    Write-Host 'CONEXION AUTORIZADA EXITOSAMENTE.' -ForegroundColor Green
    exit 0
}

$status = if ($null -eq $record) { 'no encontrado' } else { $record.Status }
Write-Host ("Aun no autorizado. Estado actual: {0}" -f $status) -ForegroundColor Red
Write-Host 'Prueba conectar por USB y vuelve a ejecutar SETUP.bat si el problema persiste.' -ForegroundColor Yellow
Read-Host 'Presiona Enter para salir'
exit 1
