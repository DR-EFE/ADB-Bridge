# Punto de entrada principal de Android ADB Tools.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$modulePath = Join-Path $PSScriptRoot 'AndroidAdb.psm1'
Import-Module $modulePath -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'AndroidTools.ps1' -Message 'Inicio del menu principal.'

function Invoke-AndroidToolScript {
    param([Parameter(Mandatory=$true)][string]$Name,[string[]]$Arguments=@())
    $path = Join-Path $PSScriptRoot $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "No existe el script '$Name'." }
    Write-AdbLog -Level INFO -Source 'AndroidTools.ps1' -Message ("Ejecutando {0}." -f $Name)
    & powershell.exe -NoProfile -File $path @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) { Write-AdbLog -Level WARN -Source 'AndroidTools.ps1' -Message ("{0} termino con codigo {1}." -f $Name,$code) }
}

function Get-MainHeader {
    Clear-Host
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '                    ANDROID ADB TOOLS' -ForegroundColor Yellow
    Write-Host '============================================================' -ForegroundColor Cyan
    try {
        $selected = Get-CurrentAdbDevice
        if ($null -eq $selected) { throw 'sin seleccion' }
        $serial = Resolve-AdbSerial -DeviceOrEndpoint $selected
        $record = @(Get-AdbDevices | Where-Object Serial -ceq $serial)[0]
        $state = if ($null -eq $record) { 'no encontrado' } else { [string]$record.Status }
        $endpoint = if ($selected.LastEndpoint) { $selected.LastEndpoint } else { $serial }
        $battery = 'no disponible'
        if ($state -ceq 'device') {
            try {
                $batteryLines = @(Invoke-AdbCommand -Serial $serial -Arguments @('shell','dumpsys','battery'))
                $levelLine = @($batteryLines | Where-Object { [string]$_ -match '^s*level:s*d+' })[0]
                if ($levelLine -match '(d+)') { $battery = "{0}%" -f $Matches[1] }
            } catch { Write-AdbLog -Level DEBUG -Source 'AndroidTools.ps1' -Serial $serial -Message ("No se pudo leer bateria: {0}" -f $_.Exception.Message) }
        }
        Write-Host ("Dispositivo: {0} | Endpoint: {1} | Estado: {2} | Bateria: {3}" -f $selected.FriendlyName,$endpoint,$state,$battery) -ForegroundColor Green
    } catch {
        Write-Host 'Dispositivo: ninguno seleccionado | Estado: no disponible' -ForegroundColor DarkYellow
    }
    Write-Host '------------------------------------------------------------'
}

try {
    while ($true) {
        Get-MainHeader
        Write-Host '1. Reconectar / Gestionar dispositivos'
        Write-Host '2. Transferir archivos'
        Write-Host '3. Explorador de archivos'
        Write-Host '4. Backup avanzado'
        Write-Host '5. Gestion de aplicaciones'
        Write-Host '6. Captura / Grabacion de pantalla'
        Write-Host '7. Sincronizacion por perfiles'
        Write-Host '8. Monitor de Notificaciones (UI Automation + IA)'
        Write-Host '9. Exportar chats de WhatsApp (primeros N)'
        Write-Host '10. Diagnostico del sistema'
        Write-Host '0. Salir'
        switch (Read-Host 'Selecciona una opcion') {
            '1' { Invoke-AndroidToolScript 'android_reconnect.ps1' }
            '2' { Invoke-AndroidToolScript 'android_transfer.ps1' }
            '3' { Invoke-AndroidToolScript 'android_file_explorer.ps1' }
            '4' { Invoke-AndroidToolScript 'android_advanced_backup.ps1' }
            '5' { Invoke-AndroidToolScript 'android_apps.ps1' }
            '6' { Invoke-AndroidToolScript 'android_screen.ps1' }
            '7' { Invoke-AndroidToolScript 'android_sync.ps1' }
            '8' { Invoke-AndroidToolScript 'android_notification_monitor.ps1' }
            '9' {
                $countInput = Read-Host 'Cuantos chats recientes deseas exportar? [Enter = 10]'
                $n = 10
                if (-not [string]::IsNullOrWhiteSpace($countInput)) {
                    [int]::TryParse($countInput, [ref]$n) | Out-Null
                    if ($n -lt 1) { $n = 10 }
                }
                $mediaChoice = Read-Host 'Incluir archivos multimedia (.zip)? (s/N)'
                $extraArgs = @('-Top', $n.ToString())
                if ($mediaChoice -match '^(s|si|y|yes)$') {
                    $extraArgs += '-IncludeMedia'
                }
                Invoke-AndroidToolScript 'android_whatsapp_export.ps1' $extraArgs
                Read-Host 'Presiona Enter para continuar' | Out-Null
            }
            '10' { Invoke-AndroidToolScript 'android_diagnostics.ps1' }
            '0' { break }
            default { Write-Host 'Opcion invalida.' -ForegroundColor Red; Start-Sleep -Seconds 1 }
        }
    }
    Write-AdbLog -Level SUCCESS -Source 'AndroidTools.ps1' -Message 'Fin del menu principal.'
} catch {
    Write-AdbLog -Level ERROR -Source 'AndroidTools.ps1' -Message $_.Exception.Message
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
