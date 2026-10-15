# Ejecuta el Monitor de Notificaciones de UI Automation integrado con Android ADB Tools.
# Respeta la seleccion de dispositivo actual de devices.json / Get-CurrentAdbDevice.
[CmdletBinding()]
param(
    [string]$Serial,
    [double]$Interval = 3.0,
    [switch]$NoAi,
    [switch]$NoWatchers,
    [switch]$NoDeviceCopy,
    [string]$DeviceCopyPath,
    [string]$LogsDir = "logs"
)

$ErrorActionPreference = 'Stop'

# Asegurar adb en PATH (si no está globalmente, usar el binario de adbutils)
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    try {
        $adbDir = (python -c "import adbutils, os; print(os.path.dirname(adbutils.adb_path()))" 2>$null)
        if ($adbDir) {
            $adbDir = $adbDir.Trim()
            if (Test-Path -LiteralPath $adbDir) {
                $env:PATH = "$adbDir;$env:PATH"
            }
        }
    } catch { }
}

if (Get-Command adb -ErrorAction SilentlyContinue) {
    try { & adb start-server 2>$null } catch { }
}

$modulePath = Join-Path $PSScriptRoot 'AndroidAdb.psm1'
Import-Module $modulePath -Force -WarningAction SilentlyContinue
Initialize-AdbLog | Out-Null

function Invoke-NotificationMonitor {
    [CmdletBinding()]
    param(
        [string]$TargetSerial,
        [double]$CheckInterval = 3.0,
        [bool]$UseAi = $true,
        [bool]$Watchers = $true,
        [bool]$CopyPhone = $true,
        [string]$PhonePath,
        [string]$LogFolder = "logs"
    )

    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '   ANDROID UI AUTOMATION - MONITOR DE NOTIFICACIONES' -ForegroundColor Yellow
    Write-Host '============================================================' -ForegroundColor Cyan

    # 1. Determinar el serial del dispositivo a usar
    $resolvedSerial = $TargetSerial
    if ([string]::IsNullOrWhiteSpace($resolvedSerial)) {
        $selected = Get-CurrentAdbDevice
        if ($null -ne $selected) {
            $resolvedSerial = Resolve-AdbSerial -DeviceOrEndpoint $selected
            Write-Host ("Dispositivo activo seleccionado: {0} ({1})" -f $selected.FriendlyName, $resolvedSerial) -ForegroundColor Green
        }
    }

    # 2. Si aún no hay serial, verificar dispositivos ADB disponibles
    $adbDevices = @(Get-AdbDevices)
    if ([string]::IsNullOrWhiteSpace($resolvedSerial)) {
        $ready = @($adbDevices | Where-Object { $_.Status -ceq 'device' })
        if ($ready.Count -eq 0) {
            Write-Host "ERROR: No hay dispositivos ADB en estado 'device'." -ForegroundColor Red
            Write-Host "Conecta tu dispositivo mediante Wireless Debugging o USB antes de iniciar el monitor." -ForegroundColor Yellow
            return
        }
        $resolvedSerial = $ready[0].Serial
        Write-Host ("Dispositivo detectado automáticamente: {0}" -f $resolvedSerial) -ForegroundColor Green
    } else {
        # Verificar estado del serial elegido
        $record = @($adbDevices | Where-Object { $_.Serial -ieq $resolvedSerial })[0]
        if ($null -eq $record -or $record.Status -cne 'device') {
            $state = if ($null -eq $record) { "desconectado" } else { $record.Status }
            Write-Host ("ERROR: El dispositivo '{0}' se encuentra en estado '{1}'." -f $resolvedSerial, $state) -ForegroundColor Red
            Write-Host "Reconéctalo antes de iniciar el monitoreo (ejecuta android_reconnect.ps1)." -ForegroundColor Yellow
            return
        }
    }

    # 3. Construir argumentos para python
    $scriptPath = Join-Path $PSScriptRoot 'monitor_notificaciones.py'
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Host "ERROR: No se encontró '$scriptPath'." -ForegroundColor Red
        return
    }

    $pyArgs = @("`"$scriptPath`"", "--serial", "`"$resolvedSerial`"", "--interval", $CheckInterval.ToString([Globalization.CultureInfo]::InvariantCulture))
    if (-not $UseAi) { $pyArgs += "--no-ai" }
    if (-not $Watchers) { $pyArgs += "--no-watchers" }
    if (-not $CopyPhone) { $pyArgs += "--no-device-copy" }
    if (-not [string]::IsNullOrWhiteSpace($PhonePath)) { $pyArgs += @("--device-copy-path", "`"$PhonePath`"") }
    if (-not [string]::IsNullOrWhiteSpace($LogFolder)) { $pyArgs += @("--logs-dir", "`"$LogFolder`"") }

    Write-Host "`nLanzando monitor en Python..." -ForegroundColor Cyan
    Write-Host "Modo IA      : $(if($UseAi){'Automático con Ollama (inicia/apaga servicio)'}else{'Desactivado (reglas)'})" -ForegroundColor Gray
    Write-Host "Endpoint ADB : $resolvedSerial`n" -ForegroundColor Gray

    # 4. Ejecutar monitor
    $cmd = "python " + ($pyArgs -join " ")
    & python $scriptPath --serial $resolvedSerial --interval $CheckInterval $(if(-not $UseAi){"--no-ai"}) $(if(-not $Watchers){"--no-watchers"}) $(if(-not $CopyPhone){"--no-device-copy"}) $(if($PhonePath){"--device-copy-path"; $PhonePath}) $(if($LogFolder){"--logs-dir"; $LogFolder})
}

# Ejecutar función
Invoke-NotificationMonitor -TargetSerial $Serial -CheckInterval $Interval -UseAi (-not $NoAi) -Watchers (-not $NoWatchers) -CopyPhone (-not $NoDeviceCopy) -PhonePath $DeviceCopyPath -LogFolder $LogsDir
