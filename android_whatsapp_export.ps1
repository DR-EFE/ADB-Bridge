<#
.SYNOPSIS
    Exporta los primeros N chats de WhatsApp utilizando la capa de UI Automation sobre ADB inalámbrico o USB.

.DESCRIPTION
    Script integrado con el sistema Android Tools (devices.json / Get-CurrentAdbDevice).
    Verifica que el dispositivo seleccionado esté en estado 'device' antes de proceder y
    ejecuta la exportación nativa descargando los archivos a la computadora local y
    limpiando los residuos en el teléfono.

.PARAMETER Top
    Número de chats más recientes a exportar (predeterminado: 10). Alias: -n

.PARAMETER IncludeMedia
    Indica si se deben incluir archivos multimedia (fotos, audios, videos) en formato .zip.
    Por defecto es solo texto (.txt).

.PARAMETER OutputDir
    Carpeta local de destino para las descargas (predeterminado: .\exports).

.PARAMETER Serial
    Serial ADB o IP:puerto del dispositivo. Si se omite, utiliza el dispositivo
    actualmente seleccionado en Android Tools (devices.json).

.EXAMPLE
    .\android_whatsapp_export.ps1 -Top 5

.EXAMPLE
    .\android_whatsapp_export.ps1 -n 10 -IncludeMedia -OutputDir "C:\Backups\WhatsApp"

.EXAMPLE
    .\android_whatsapp_export.ps1 -Serial "192.168.1.50:5555" -Top 3
#>

[CmdletBinding()]
param(
    [Alias('n')]
    [int]$Top = 10,

    [switch]$IncludeMedia,

    [switch]$ProcessChats,

    [ValidateSet('All', 'Excel', 'Json', 'Csv')]
    [string]$Format = 'All',

    [string]$MyName,

    [string]$OutputDir = ".\exports",

    [string]$Serial,

    [switch]$Help
)

if ($Help) {
    Get-Help $PSCommandPath -Detailed
    return
}

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

function Invoke-WhatsAppExport {
    [CmdletBinding()]
    param(
        [int]$Limit = 10,
        [bool]$Media = $false,
        [bool]$Process = $false,
        [string]$ExportFormat = 'All',
        [string]$OwnName,
        [string]$Destination = ".\exports",
        [string]$TargetSerial
    )

    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '   ANDROID UI AUTOMATION - EXPORTACION DE WHATSAPP' -ForegroundColor Yellow
    Write-Host '============================================================' -ForegroundColor Cyan

    # 1. Resolver el serial del dispositivo
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
            Write-Host "Conecta tu dispositivo mediante Wireless Debugging o USB antes de iniciar la exportación." -ForegroundColor Yellow
            return
        }
        $resolvedSerial = $ready[0].Serial
        Write-Host ("Dispositivo detectado automáticamente: {0}" -f $resolvedSerial) -ForegroundColor Green
    }

    # 3. Validar estado 'device' mediante Test-AdbDeviceReady
    Write-Host "Verificando estado del dispositivo ($resolvedSerial)..." -ForegroundColor Gray
    $isReady = Test-AdbDeviceReady -Device $resolvedSerial -AutoReconnect $true
    if (-not $isReady) {
        Write-Host "ERROR: El dispositivo '$resolvedSerial' no está en estado 'device' o no responde." -ForegroundColor Red
        Write-Host "Verifica la conexión o ejecuta 'android_reconnect.ps1' para restablecer la sesión ADB." -ForegroundColor Yellow
        return
    }

    # 4. Localizar script de python
    $scriptPath = Join-Path $PSScriptRoot 'export_top_chats.py'
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Host "ERROR: No se encontró el script de exportación: '$scriptPath'." -ForegroundColor Red
        return
    }

    # 5. Crear carpeta de salida si no existe
    $fullOutputDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Destination))
    if (-not (Test-Path -LiteralPath $fullOutputDir)) {
        New-Item -ItemType Directory -Path $fullOutputDir -Force | Out-Null
    }

    Write-Host "`nIniciando exportación de WhatsApp..." -ForegroundColor Cyan
    Write-Host "Chats a exportar : $Limit" -ForegroundColor Gray
    Write-Host "Archivos medios  : $(if($Media){'Sí (.zip)'}else{'No (.txt)'})" -ForegroundColor Gray
    Write-Host "Procesar chats   : $(if($Process){"Sí ($ExportFormat)"}else{'No'})" -ForegroundColor Gray
    Write-Host "Carpeta destino  : $fullOutputDir" -ForegroundColor Gray
    Write-Host "Dispositivo ADB  : $resolvedSerial`n" -ForegroundColor Gray

    # 6. Invocar export_top_chats.py
    try {
        $cliArgs = @($scriptPath, '-n', $Limit.ToString(), '--output-dir', $fullOutputDir, '--serial', $resolvedSerial)
        if ($Media) { $cliArgs += '--include-media' }
        if ($Process) {
            $cliArgs += '--process'
            $cliArgs += '--format'
            $cliArgs += $ExportFormat.ToLowerInvariant()
            if (-not [string]::IsNullOrWhiteSpace($OwnName)) { $cliArgs += '--my-name'; $cliArgs += $OwnName }
        }
        & python @cliArgs
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-Host "`nProceso de exportación finalizado exitosamente." -ForegroundColor Green
        } else {
            Write-Host "`nEl proceso de exportación finalizó con código $exitCode." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "`nERROR al ejecutar la exportación: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Invocar la función con los parámetros recibidos
Invoke-WhatsAppExport -Limit $Top -Media $IncludeMedia.IsPresent -Process $ProcessChats.IsPresent -ExportFormat $Format -OwnName $MyName -Destination $OutputDir -TargetSerial $Serial
