<#
.SYNOPSIS
    Android ADB Tools - Interfaz Grafica Moderna (WinForms).
.DESCRIPTION
    Panel de control moderno y unificado para Android ADB Tools en PowerShell 5.1.
    Permite operar dispositivos, transferir archivos, respaldos, capturas, diagnostico
    y ejecucion avanzada de comandos ADB con sistema de favoritos persistente en JSON.
#>

#Requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

# Cargar ensamblados de Windows Forms y GDI+
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# Rutas del proyecto y modulo ADB
$script:RootPath = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($script:RootPath)) {
    $script:RootPath = (Get-Location).Path
}

$script:ModulePath = Join-Path $script:RootPath 'AndroidAdb.psm1'
if (Test-Path -LiteralPath $script:ModulePath) {
    Import-Module $script:ModulePath -Force -DisableNameChecking
    Initialize-AdbLog | Out-Null
    Write-AdbLog -Level INFO -Source 'AndroidTools.GUI.ps1' -Message 'Iniciando interfaz grafica WinForms moderna.'
}

# Guarda desde la GUI sin crear duplicados al repetir el asistente. La clave
# estable del modo USB es UsbSerial; en modo TCP/IP lo es LastEndpoint.
function Save-GuiAdbDevice {
    param(
        [string]$FriendlyName,
        [Parameter(Mandatory=$true)][string]$Mode,
        [string]$UsbSerial,
        [string]$LastEndpoint,
        [bool]$IsPaired = $false
    )

    if ([string]::IsNullOrWhiteSpace($FriendlyName)) {
        $FriendlyName = if (-not [string]::IsNullOrWhiteSpace($UsbSerial)) { $UsbSerial } elseif (-not [string]::IsNullOrWhiteSpace($LastEndpoint)) { $LastEndpoint } else { 'Dispositivo Android' }
    }

    $existing = @(Get-SavedAdbDevices | Where-Object {
        ($UsbSerial -and $_.UsbSerial -ceq $UsbSerial) -or
        ($LastEndpoint -and $_.LastEndpoint -ieq $LastEndpoint)
    })[0]
    $timestamp = [DateTime]::UtcNow.ToString('o')
    if ($null -ne $existing) {
        return (Update-SavedAdbDevice -Id $existing.Id -FriendlyName $FriendlyName -UsbSerial $UsbSerial -LastEndpoint $LastEndpoint -Mode $Mode -IsPaired $IsPaired -LastConnectedUtc $timestamp)
    }
    return (Add-SavedAdbDevice -FriendlyName $FriendlyName -Mode $Mode -UsbSerial $UsbSerial -LastEndpoint $LastEndpoint -IsPaired $IsPaired -LastConnectedUtc $timestamp)
}

# Dispositivos USB ya consultados durante esta conexión física. Se elimina la
# marca al desconectarlos, de forma que volver a enchufarlos muestra de nuevo
# la confirmación sin abrir una cascada de cuadros de diálogo.
$script:UsbPromptedSerials = @{}
$script:UsbAutoScanBusy = $false

function Start-UsbWirelessConnection {
    param([Parameter(Mandatory=$true)][string]$UsbSerial)

    Invoke-GuiAsyncTask -Title "Preparar WiFi para $UsbSerial" -WorkerScript {
        param($serial)
        $ip = Get-AdbDeviceIpv4 -Serial $serial
        if ([string]::IsNullOrWhiteSpace($ip)) {
            throw 'No fue posible obtener la IP WiFi del teléfono mientras estaba conectado por USB.'
        }
        Invoke-Adb -Serial $serial -Arguments @('tcpip','5555') | Out-Null
        Start-Sleep -Seconds 1
        $connected = Connect-AdbEndpoint -Endpoint ("{0}:5555" -f $ip) -TimeoutSeconds 15
        return [pscustomobject]@{ UsbSerial=$serial; Endpoint=("{0}:5555" -f $ip); Record=$connected }
    } -Parameters @{ serial=$UsbSerial } -OnCompleted {
        param($result)
        if ($null -eq $result -or -not $result.Success) {
            $message = if ($result -and $result.Error) { $result.Error } else { 'No se pudo activar ADB por WiFi.' }
            [System.Windows.Forms.MessageBox]::Show($message, 'No se pudo conectar por WiFi', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
            return
        }
        try {
            $info = $result.Output[0]
            $saved = Save-GuiAdbDevice -FriendlyName "$($info.UsbSerial) (WiFi)" -Mode 'tcpip-5555' -UsbSerial $info.UsbSerial -LastEndpoint $info.Record.Serial -IsPaired $false
            Set-CurrentAdbDevice -Device $saved -Persist | Out-Null
            Write-LogEntry -Level 'SUCCESS' -Message "Dispositivo conectado por WiFi en $($info.Endpoint)."
            [System.Windows.Forms.MessageBox]::Show("Conectado por WiFi en $($info.Endpoint).`n`nAhora puedes desconectar el cable USB.", 'Modo inalámbrico listo', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
            Refresh-DeviceStatus -Silent
        } catch {
            [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'No se pudo guardar la conexión WiFi', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
        }
    }
}

function Invoke-UsbAutoDetection {
    if ($script:UsbAutoScanBusy -or $script:RunningAsyncTasks -gt 0) { return }
    $script:UsbAutoScanBusy = $true
    try {
        $usbDevices = @(Get-AdbDevices | Where-Object { $_.Status -ceq 'device' -and $_.Serial -notmatch ':\d+$' })
        $connectedSerials = @($usbDevices | ForEach-Object { $_.Serial })
        foreach ($knownSerial in @($script:UsbPromptedSerials.Keys)) {
            if ($connectedSerials -notcontains $knownSerial) { $script:UsbPromptedSerials.Remove($knownSerial) }
        }

        foreach ($usbDevice in $usbDevices) {
            if ($script:UsbPromptedSerials.ContainsKey($usbDevice.Serial)) { continue }

            try { Set-CurrentAdbDevice -Device ([pscustomobject]@{ Serial=$usbDevice.Serial }) | Out-Null } catch { }
            $script:UsbPromptedSerials[$usbDevice.Serial] = $true
            $answer = [System.Windows.Forms.MessageBox]::Show(
                "Se detectó el teléfono '$($usbDevice.Serial)' por USB.`n`n¿Quieres conectarlo por WiFi para poder retirar el cable?`n`nSí: activa TCP/IP 5555 y guarda la conexión inalámbrica.`nNo: queda disponible mientras el cable siga conectado.",
                'Dispositivo USB detectado',
                [System.Windows.Forms.MessageBoxButtons]::YesNo,
                [System.Windows.Forms.MessageBoxIcon]::Question
            )
            if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
                Start-UsbWirelessConnection -UsbSerial $usbDevice.Serial
            }
        }
    } catch {
        Write-LogEntry -Level 'DEBUG' -Message "Escaneo USB no disponible: $($_.Exception.Message)"
    } finally {
        $script:UsbAutoScanBusy = $false
    }
}

# -----------------------------------------------------------------------------
# PALETA DE COLORES Y ESTILOS MODERNOS (Segoe UI + Tailwind/Modern Palette)
# -----------------------------------------------------------------------------
$script:Theme = @{
    BgWindow        = [System.Drawing.ColorTranslator]::FromHtml('#F8F9FA')
    BgCard          = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    BgHeader        = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    BorderCard      = [System.Drawing.ColorTranslator]::FromHtml('#E2E8F0')
    BorderLight     = [System.Drawing.ColorTranslator]::FromHtml('#F1F5F9')
    
    # Acentos
    Primary         = [System.Drawing.ColorTranslator]::FromHtml('#2563EB') # Azul moderno
    PrimaryHover    = [System.Drawing.ColorTranslator]::FromHtml('#1D4ED8')
    PrimaryActive   = [System.Drawing.ColorTranslator]::FromHtml('#1E40AF')
    PrimaryFg       = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    
    Secondary       = [System.Drawing.ColorTranslator]::FromHtml('#F1F5F9')
    SecondaryHover  = [System.Drawing.ColorTranslator]::FromHtml('#E2E8F0')
    SecondaryFg     = [System.Drawing.ColorTranslator]::FromHtml('#334155')
    
    # Botones de Acciones
    BtnActionBg     = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    BtnActionHover  = [System.Drawing.ColorTranslator]::FromHtml('#EFF6FF')
    BtnActionBorder = [System.Drawing.ColorTranslator]::FromHtml('#CBD5E1')
    BtnActionFg     = [System.Drawing.ColorTranslator]::FromHtml('#1E293B')
    
    # Textos
    TextTitle       = [System.Drawing.ColorTranslator]::FromHtml('#0F172A')
    TextBody        = [System.Drawing.ColorTranslator]::FromHtml('#334155')
    TextMuted       = [System.Drawing.ColorTranslator]::FromHtml('#64748B')
    
    # Terminal / Log
    LogBg           = [System.Drawing.ColorTranslator]::FromHtml('#0F172A') # Slate 900
    LogFg           = [System.Drawing.ColorTranslator]::FromHtml('#F8FAFC')
    
    # Badges de estado
    BadgeSuccessBg  = [System.Drawing.ColorTranslator]::FromHtml('#DCFCE7')
    BadgeSuccessFg  = [System.Drawing.ColorTranslator]::FromHtml('#15803D')
    BadgeWarningBg  = [System.Drawing.ColorTranslator]::FromHtml('#FEF3C7')
    BadgeWarningFg  = [System.Drawing.ColorTranslator]::FromHtml('#B45309')
    BadgeMutedBg    = [System.Drawing.ColorTranslator]::FromHtml('#F1F5F9')
    BadgeMutedFg    = [System.Drawing.ColorTranslator]::FromHtml('#64748B')
}

$script:Fonts = @{
    TitleLarge = New-Object System.Drawing.Font('Segoe UI', 13, [System.Drawing.FontStyle]::Bold)
    TitleMid   = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    TitleSmall = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    Body       = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Regular)
    BodyBold   = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Bold)
    Small      = New-Object System.Drawing.Font('Segoe UI', 8.5, [System.Drawing.FontStyle]::Regular)
    Badge      = New-Object System.Drawing.Font('Segoe UI', 8.5, [System.Drawing.FontStyle]::Bold)
    Code       = New-Object System.Drawing.Font('Consolas', 9.5, [System.Drawing.FontStyle]::Regular)
}

# -----------------------------------------------------------------------------
# GESTOR DE COMANDOS FAVORITOS (custom-commands.json)
# -----------------------------------------------------------------------------
function Get-AdbFavoritesPath {
    $toolsDir = Join-Path $env:USERPROFILE 'AndroidTools'
    if (-not (Test-Path -LiteralPath $toolsDir)) {
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    }
    return (Join-Path $toolsDir 'custom-commands.json')
}

function Get-AdbFavoriteCommands {
    $path = Get-AdbFavoritesPath
    if (Test-Path -LiteralPath $path) {
        try {
            $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                $items = @($raw | ConvertFrom-Json)
                if ($items.Count -gt 0) { return $items }
            }
        } catch { }
    }

    # Comandos iniciales por defecto si no existe el archivo
    $defaults = @(
        [pscustomobject]@{ Name = '📱 Modelo y dispositivo'; Command = 'shell getprop ro.product.model' },
        [pscustomobject]@{ Name = '🤖 Version de Android'; Command = 'shell getprop ro.build.version.release' },
        [pscustomobject]@{ Name = '🔋 Estado de bateria'; Command = 'shell dumpsys battery' },
        [pscustomobject]@{ Name = '💾 Espacio en almacenamiento'; Command = 'shell df -h /sdcard' },
        [pscustomobject]@{ Name = '🌐 Direccion IP (wlan0)'; Command = 'shell ip -4 addr show wlan0' },
        [pscustomobject]@{ Name = '📦 Apps instaladas (usuario)'; Command = 'shell pm list packages -3' },
        [pscustomobject]@{ Name = '🧠 Uso de memoria RAM'; Command = 'shell dumpsys meminfo' },
        [pscustomobject]@{ Name = '📋 Dispositivos adb'; Command = 'devices -l' },
        [pscustomobject]@{ Name = '🔄 Reiniciar dispositivo'; Command = 'reboot' }
    )

    Save-AdbFavoriteCommands -Commands $defaults
    return $defaults
}

function Save-AdbFavoriteCommands {
    param([array]$Commands)
    $path = Get-AdbFavoritesPath
    try {
        $json = $Commands | ConvertTo-Json -Depth 4
        [System.IO.File]::WriteAllText($path, $json, [System.Text.Encoding]::UTF8)
    } catch {
        Write-Warning "No se pudieron guardar los comandos favoritos: $($_.Exception.Message)"
    }
}

# -----------------------------------------------------------------------------
# EJECUTOR ASINCRONO SEGURO PARA WINFORMS (Sin congelar interfaz)
# -----------------------------------------------------------------------------
$script:RunningAsyncTasks = 0

function Invoke-GuiAsyncTask {
    param(
        [Parameter(Mandatory=$true)][string]$Title,
        [Parameter(Mandatory=$true)][scriptblock]$WorkerScript,
        [hashtable]$Parameters = @{},
        [scriptblock]$OnCompleted = $null
    )

    $script:RunningAsyncTasks++
    Set-UiBusyState -IsBusy $true -Operation $Title
    Write-LogEntry -Level 'INFO' -Message "Iniciando: $Title..."

    $ps = [powershell]::Create()
    $wrapper = {
        param($mPath, $subScript, $pHash)
        $result = [ordered]@{ Success = $false; Output = @(); Error = $null }
        try {
            if (Test-Path -LiteralPath $mPath) {
                Import-Module $mPath -Force -DisableNameChecking
            }
            $out = & $subScript @pHash
            $result.Output = @($out)
            $result.Success = $true
        } catch {
            $result.Error = $_.Exception.Message
            $result.Success = $false
        }
        return [pscustomobject]$result
    }

    $null = $ps.AddScript($wrapper).AddArgument($script:ModulePath).AddArgument($WorkerScript).AddArgument($Parameters)
    $asyncHandle = $ps.BeginInvoke()

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 100
    $timer.Add_Tick({
        if ($asyncHandle.IsCompleted) {
            $timer.Stop()
            $timer.Dispose()
            $script:RunningAsyncTasks--

            try {
                $execResult = $ps.EndInvoke($asyncHandle)
                $resObj = if ($execResult) { $execResult[0] } else { $null }

                if ($script:RunningAsyncTasks -le 0) {
                    $script:RunningAsyncTasks = 0
                    Set-UiBusyState -IsBusy $false -Operation $Title
                }

                if ($resObj -and $resObj.Success) {
                    Write-LogEntry -Level 'SUCCESS' -Message "$Title completado con exito."
                } elseif ($resObj -and $resObj.Error) {
                    Write-LogEntry -Level 'ERROR' -Message "$Title fallo: $($resObj.Error)"
                }

                if ($null -ne $OnCompleted) {
                    & $OnCompleted $resObj
                }
            } catch {
                Write-LogEntry -Level 'ERROR' -Message "Error al finalizar $Title : $($_.Exception.Message)"
                Set-UiBusyState -IsBusy $false -Operation $Title
            } finally {
                $ps.Dispose()
            }
        }
    })
    $timer.Start()
}

function Set-UiBusyState {
    param([bool]$IsBusy, [string]$Operation)
    if ($IsBusy) {
        $lblLastOp.Text = "Ultima operacion: $Operation (en progreso...)"
        $lblLastOp.ForeColor = $script:Theme.Primary
        $pbProgress.Visible = $true
        $pbProgress.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
        $btnReconnect.Enabled = $false
        $btnRefresh.Enabled = $false
        $btnExecuteCmd.Enabled = $false
    } else {
        $timeStr = [DateTime]::Now.ToString('HH:mm:ss')
        $lblLastOp.Text = "Ultima operacion: $Operation ($timeStr)"
        $lblLastOp.ForeColor = $script:Theme.TextMuted
        $pbProgress.Visible = $false
        $btnReconnect.Enabled = $true
        $btnRefresh.Enabled = $true
        $btnExecuteCmd.Enabled = $true
    }
}

# -----------------------------------------------------------------------------
# HELPER DE LOG CON COLOREADO MODERNO
# -----------------------------------------------------------------------------
function Write-LogEntry {
    param(
        [Parameter(Mandatory=$true)][string]$Message,
        [ValidateSet('INFO','SUCCESS','WARN','ERROR','CMD')][string]$Level = 'INFO'
    )

    if ($null -eq $txtLog -or $txtLog.IsDisposed) { return }

    $timeStr = [DateTime]::Now.ToString('HH:mm:ss')
    $color = switch ($Level) {
        'SUCCESS' { [System.Drawing.ColorTranslator]::FromHtml('#10B981') } # Verde
        'ERROR'   { [System.Drawing.ColorTranslator]::FromHtml('#F87171') } # Rojo
        'WARN'    { [System.Drawing.ColorTranslator]::FromHtml('#FBBF24') } # Ambar
        'CMD'     { [System.Drawing.ColorTranslator]::FromHtml('#60A5FA') } # Azul cielo
        default   { [System.Drawing.ColorTranslator]::FromHtml('#E2E8F0') } # Blanco/Gris
    }

    $txtLog.SelectionStart = $txtLog.TextLength
    $txtLog.SelectionLength = 0
    $txtLog.SelectionColor = [System.Drawing.ColorTranslator]::FromHtml('#94A3B8')
    $txtLog.AppendText("[$timeStr] ")

    $txtLog.SelectionStart = $txtLog.TextLength
    $txtLog.SelectionLength = 0
    $txtLog.SelectionColor = $color
    $txtLog.AppendText("[$Level] ")

    $txtLog.SelectionStart = $txtLog.TextLength
    $txtLog.SelectionLength = 0
    $txtLog.SelectionColor = if ($Level -eq 'CMD') { [System.Drawing.ColorTranslator]::FromHtml('#93C5FD') } else { [System.Drawing.ColorTranslator]::FromHtml('#F8FAFC') }
    $txtLog.AppendText("$Message`r`n")

    $txtLog.ScrollToCaret()

    # Escribir tambien al log persistente del modulo si esta disponible
    try {
        if (Get-Command Write-AdbLog -ErrorAction SilentlyContinue) {
            $logLevel = if ($Level -eq 'CMD') { 'INFO' } else { $Level }
            Write-AdbLog -Level $logLevel -Source 'GUI' -Message $Message
        }
    } catch { }
}

# -----------------------------------------------------------------------------
# ACTUALIZACION DEL ESTADO DEL DISPOSITIVO Y CABECERA
# -----------------------------------------------------------------------------
function Refresh-DeviceStatus {
    param([switch]$Silent)

    if (-not $Silent) {
        Write-LogEntry -Level 'INFO' -Message 'Consultando dispositivos ADB...'
    }

    $worker = {
        $devInfo = [ordered]@{
            HasDevice     = $false
            FriendlyName  = 'Ningun dispositivo seleccionado'
            Serial        = $null
            Endpoint      = 'Desconectado'
            Status        = 'desconectado'
            Battery       = 'N/A'
            Temperature   = 'N/A'
            Model         = $null
            AndroidVer    = $null
            AdbAvailable  = $false
        }

        if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
            $devInfo.FriendlyName = 'ADB no encontrado en PATH'
            return [pscustomobject]$devInfo
        }
        $devInfo.AdbAvailable = $true

        $cur = Get-CurrentAdbDevice
        $onlineDevices = @(Get-AdbDevices)

        # Si no hay actual, pero hay exactamente un dispositivo disponible en estado device, seleccionarlo
        if ($null -eq $cur -and $onlineDevices.Count -gt 0) {
            $candidate = @($onlineDevices | Where-Object { $_.Status -ceq 'device' })[0]
            if ($null -ne $candidate) {
                try {
                    $cur = Set-CurrentAdbDevice -Device $candidate -Persist
                } catch { }
            }
        }

        if ($null -ne $cur) {
            $devInfo.HasDevice = $true
            $devInfo.Serial = $cur.Serial
            $devInfo.FriendlyName = if ($cur.FriendlyName) { $cur.FriendlyName } else { $cur.Serial }
            $devInfo.Endpoint = if ($cur.LastEndpoint) { $cur.LastEndpoint } else { $cur.Serial }
            
            # Verificar estado actual contra adb devices
            $record = @($onlineDevices | Where-Object { $_.Serial -ceq $cur.Serial })[0]
            $devInfo.Status = if ($null -ne $record) { [string]$record.Status } else { 'offline' }

            if ($devInfo.Status -ceq 'device') {
                # Obtener modelo y version
                try {
                    $m = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','getprop','ro.product.model'))
                    $devInfo.Model = ($m -join ' ').Trim()
                } catch { }
                try {
                    $v = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','getprop','ro.build.version.release'))
                    $devInfo.AndroidVer = ($v -join ' ').Trim()
                } catch { }

                # Obtener bateria y temperatura
                try {
                    $batteryDump = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','dumpsys','battery'))
                    $lvlLine = @($batteryDump | Where-Object { [string]$_ -match '^\s*level:\s*(\d+)' })[0]
                    if ($lvlLine -match '(\d+)') {
                        $devInfo.Battery = "{0}%" -f $Matches[1]
                    }
                    $tempLine = @($batteryDump | Where-Object { [string]$_ -match '^\s*temperature:\s*(\d+)' })[0]
                    if ($tempLine -match '(\d+)') {
                        $val = [double]$Matches[1] / 10.0
                        $devInfo.Temperature = "{0:N1} °C" -f $val
                    }
                } catch { }
            }
        } elseif ($onlineDevices.Count -gt 0) {
            $first = $onlineDevices[0]
            $devInfo.FriendlyName = "Dispositivo no seleccionado: $($first.Serial)"
            $devInfo.Status = $first.Status
            $devInfo.Endpoint = $first.Serial
        }

        return [pscustomobject]$devInfo
    }

    Invoke-GuiAsyncTask -Title 'Actualizar Estado' -WorkerScript $worker -OnCompleted {
        param($res)
        if ($null -eq $res -or -not $res.Success) {
            $lblDeviceName.Text = "Error al consultar ADB"
            $lblStatusBadge.Text = "● ERROR"
            $lblStatusBadge.BackColor = $script:Theme.BadgeWarningBg
            $lblStatusBadge.ForeColor = $script:Theme.BadgeWarningFg
            return
        }

        $info = $res.Output[0]
        if (-not $info.AdbAvailable) {
            $lblDeviceName.Text = "⚠️ ADB no disponible en PATH"
            $lblStatusBadge.Text = "● SIN ADB"
            $lblStatusBadge.BackColor = $script:Theme.BadgeWarningBg
            $lblStatusBadge.ForeColor = $script:Theme.BadgeWarningFg
            $lblEndpoint.Text = "Instala Android Platform-Tools"
            $lblBatteryTemp.Text = "No disponible"
            Write-LogEntry -Level 'WARN' -Message "ADB no se encuentra en PATH. Instala Android Platform Tools."
            return
        }

        # Actualizar Nombre
        if ($info.Model) {
            $lblDeviceName.Text = "$($info.FriendlyName) ($($info.Model))"
        } else {
            $lblDeviceName.Text = $info.FriendlyName
        }

        # Actualizar Badge
        switch ($info.Status) {
            'device' {
                $lblStatusBadge.Text = "● CONECTADO"
                $lblStatusBadge.BackColor = $script:Theme.BadgeSuccessBg
                $lblStatusBadge.ForeColor = $script:Theme.BadgeSuccessFg
            }
            'offline' {
                $lblStatusBadge.Text = "● OFFLINE"
                $lblStatusBadge.BackColor = $script:Theme.BadgeWarningBg
                $lblStatusBadge.ForeColor = $script:Theme.BadgeWarningFg
            }
            'unauthorized' {
                $lblStatusBadge.Text = "● NO AUTORIZADO"
                $lblStatusBadge.BackColor = $script:Theme.BadgeWarningBg
                $lblStatusBadge.ForeColor = $script:Theme.BadgeWarningFg
            }
            default {
                $lblStatusBadge.Text = "○ DESCONECTADO"
                $lblStatusBadge.BackColor = $script:Theme.BadgeMutedBg
                $lblStatusBadge.ForeColor = $script:Theme.BadgeMutedFg
            }
        }

        # Endpoint
        $lblEndpoint.Text = "🌐 $($info.Endpoint)"

        # Bateria y Temperatura
        if ($info.Battery -ne 'N/A' -or $info.Temperature -ne 'N/A') {
            $lblBatteryTemp.Text = "🔋 $($info.Battery)   🌡️ $($info.Temperature)"
        } else {
            $lblBatteryTemp.Text = "🔋 Bateria: N/A"
        }

        if ($info.HasDevice -and $info.Status -eq 'device') {
            $andTxt = if ($info.AndroidVer) { " (Android $($info.AndroidVer))" } else { '' }
            Write-LogEntry -Level 'INFO' -Message "Dispositivo activo: $($info.FriendlyName)$andTxt | Endpoint: $($info.Endpoint)"
        }
    }
}

# -----------------------------------------------------------------------------
# CONSTRUCCION DE LA VENTANA PRINCIPAL (WinForms)
# -----------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "Android ADB Tools - Control Center"
$form.Size = New-Object System.Drawing.Size(1120, 760)
$form.MinimumSize = New-Object System.Drawing.Size(980, 680)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.BackColor = $script:Theme.BgWindow
$form.Font = $script:Fonts.Body

# =============================================================================
# 1. CABECERA (Top Header Panel)
# =============================================================================
$pnlHeader = New-Object System.Windows.Forms.Panel
$pnlHeader.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlHeader.Height = 90
$pnlHeader.BackColor = $script:Theme.BgHeader
$pnlHeader.Padding = New-Object System.Windows.Forms.Padding(20, 12, 20, 12)

# Linea divisoria inferior de la cabecera
$pnlHeaderBorder = New-Object System.Windows.Forms.Panel
$pnlHeaderBorder.Dock = [System.Windows.Forms.DockStyle]::Bottom
$pnlHeaderBorder.Height = 1
$pnlHeaderBorder.BackColor = $script:Theme.BorderCard
$pnlHeader.Controls.Add($pnlHeaderBorder)

# Icono grande de dispositivo en cabecera
$lblHeaderIcon = New-Object System.Windows.Forms.Label
$lblHeaderIcon.Text = "📱"
$lblHeaderIcon.Font = New-Object System.Drawing.Font('Segoe UI Emoji', 24)
$lblHeaderIcon.Location = New-Object System.Drawing.Point(18, 18)
$lblHeaderIcon.Size = New-Object System.Drawing.Size(48, 50)
$lblHeaderIcon.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$pnlHeader.Controls.Add($lblHeaderIcon)

# Nombre del Dispositivo
$lblDeviceName = New-Object System.Windows.Forms.Label
$lblDeviceName.Text = "Cargando dispositivo..."
$lblDeviceName.Font = $script:Fonts.TitleLarge
$lblDeviceName.ForeColor = $script:Theme.TextTitle
$lblDeviceName.Location = New-Object System.Drawing.Point(74, 16)
$lblDeviceName.Size = New-Object System.Drawing.Size(560, 28)
$pnlHeader.Controls.Add($lblDeviceName)

# Fila de Estado: Badge + Endpoint + Bateria
$pnlStatusRow = New-Object System.Windows.Forms.FlowLayoutPanel
$pnlStatusRow.Location = New-Object System.Drawing.Point(74, 48)
$pnlStatusRow.Size = New-Object System.Drawing.Size(650, 30)
$pnlStatusRow.WrapContents = $false
$pnlStatusRow.FlowDirection = [System.Windows.Forms.FlowDirection]::LeftToRight

$lblStatusBadge = New-Object System.Windows.Forms.Label
$lblStatusBadge.Text = "○ CONSULTANDO"
$lblStatusBadge.Font = $script:Fonts.Badge
$lblStatusBadge.BackColor = $script:Theme.BadgeMutedBg
$lblStatusBadge.ForeColor = $script:Theme.BadgeMutedFg
$lblStatusBadge.Padding = New-Object System.Windows.Forms.Padding(8, 3, 8, 3)
$lblStatusBadge.AutoSize = $true
$lblStatusBadge.Margin = New-Object System.Windows.Forms.Padding(0, 0, 12, 0)
$lblStatusBadge.Cursor = [System.Windows.Forms.Cursors]::Hand
$lblStatusBadge.Add_Click({ Show-NewDeviceWizard -InitialTab 'Auth' })
$pnlStatusRow.Controls.Add($lblStatusBadge)

$lblEndpoint = New-Object System.Windows.Forms.Label
$lblEndpoint.Text = "🌐 Endpoint: Buscando..."
$lblEndpoint.Font = $script:Fonts.Body
$lblEndpoint.ForeColor = $script:Theme.TextMuted
$lblEndpoint.AutoSize = $true
$lblEndpoint.Margin = New-Object System.Windows.Forms.Padding(0, 3, 16, 0)
$pnlStatusRow.Controls.Add($lblEndpoint)

$lblBatteryTemp = New-Object System.Windows.Forms.Label
$lblBatteryTemp.Text = "🔋 Bateria: --"
$lblBatteryTemp.Font = $script:Fonts.Body
$lblBatteryTemp.ForeColor = $script:Theme.TextMuted
$lblBatteryTemp.AutoSize = $true
$lblBatteryTemp.Margin = New-Object System.Windows.Forms.Padding(0, 3, 0, 0)
$pnlStatusRow.Controls.Add($lblBatteryTemp)

$pnlHeader.Controls.Add($pnlStatusRow)

# Botones de Cabecera (Derecha): Conectar Nuevo + Reconectar + Refrescar
$btnConnectDevice = New-Object System.Windows.Forms.Button
$btnConnectDevice.Text = "➕ Conectar Nuevo"
$btnConnectDevice.Font = $script:Fonts.BodyBold
$btnConnectDevice.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
$btnConnectDevice.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
$btnConnectDevice.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnConnectDevice.FlatAppearance.BorderSize = 0
$btnConnectDevice.Size = New-Object System.Drawing.Size(160, 42)
$btnConnectDevice.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnConnectDevice.Location = New-Object System.Drawing.Point(615, 22)
$btnConnectDevice.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnConnectDevice.Add_MouseEnter({ $btnConnectDevice.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#059669') })
$btnConnectDevice.Add_MouseLeave({ $btnConnectDevice.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981') })
$pnlHeader.Controls.Add($btnConnectDevice)

$btnReconnect = New-Object System.Windows.Forms.Button
$btnReconnect.Text = "⚡ Reconectar"
$btnReconnect.Font = $script:Fonts.BodyBold
$btnReconnect.BackColor = $script:Theme.Primary
$btnReconnect.ForeColor = $script:Theme.PrimaryFg
$btnReconnect.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnReconnect.FlatAppearance.BorderSize = 0
$btnReconnect.Size = New-Object System.Drawing.Size(130, 42)
$btnReconnect.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnReconnect.Location = New-Object System.Drawing.Point(785, 22)
$btnReconnect.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnReconnect.Add_MouseEnter({ $btnReconnect.BackColor = $script:Theme.PrimaryHover })
$btnReconnect.Add_MouseLeave({ $btnReconnect.BackColor = $script:Theme.Primary })
$pnlHeader.Controls.Add($btnReconnect)

$btnRefresh = New-Object System.Windows.Forms.Button
$btnRefresh.Text = "🔄 Refrescar"
$btnRefresh.Font = $script:Fonts.BodyBold
$btnRefresh.BackColor = $script:Theme.Secondary
$btnRefresh.ForeColor = $script:Theme.SecondaryFg
$btnRefresh.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnRefresh.FlatAppearance.BorderSize = 1
$btnRefresh.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnRefresh.Size = New-Object System.Drawing.Size(110, 42)
$btnRefresh.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnRefresh.Location = New-Object System.Drawing.Point(925, 22)
$btnRefresh.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnRefresh.Add_MouseEnter({ $btnRefresh.BackColor = $script:Theme.SecondaryHover })
$btnRefresh.Add_MouseLeave({ $btnRefresh.BackColor = $script:Theme.Secondary })
$pnlHeader.Controls.Add($btnRefresh)

$form.Controls.Add($pnlHeader)

# =============================================================================
# 4. ZONA INFERIOR - COMANDOS PERSONALIZADOS (MODO AVANZADO)
# =============================================================================
$pnlAdvanced = New-Object System.Windows.Forms.Panel
$pnlAdvanced.Dock = [System.Windows.Forms.DockStyle]::Bottom
$pnlAdvanced.Height = 150
$pnlAdvanced.BackColor = $script:Theme.BgCard
$pnlAdvanced.Padding = New-Object System.Windows.Forms.Padding(20, 10, 20, 12)

# Borde superior de la zona inferior
$pnlAdvBorder = New-Object System.Windows.Forms.Panel
$pnlAdvBorder.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlAdvBorder.Height = 1
$pnlAdvBorder.BackColor = $script:Theme.BorderCard
$pnlAdvanced.Controls.Add($pnlAdvBorder)

# Fila 1 Avanzado: Titulo + Checkbox favorito
$lblAdvTitle = New-Object System.Windows.Forms.Label
$lblAdvTitle.Text = "⚡ Modo Avanzado: Consola de Comandos ADB"
$lblAdvTitle.Font = $script:Fonts.TitleSmall
$lblAdvTitle.ForeColor = $script:Theme.TextTitle
$lblAdvTitle.Location = New-Object System.Drawing.Point(20, 14)
$lblAdvTitle.Size = New-Object System.Drawing.Size(350, 20)
$pnlAdvanced.Controls.Add($lblAdvTitle)

$lblAdvSub = New-Object System.Windows.Forms.Label
$lblAdvSub.Text = "(Se ejecuta automaticamente con 'adb -s <serial>')"
$lblAdvSub.Font = $script:Fonts.Small
$lblAdvSub.ForeColor = $script:Theme.TextMuted
$lblAdvSub.Location = New-Object System.Drawing.Point(375, 16)
$lblAdvSub.Size = New-Object System.Drawing.Size(320, 18)
$pnlAdvanced.Controls.Add($lblAdvSub)

$chkFavorite = New-Object System.Windows.Forms.CheckBox
$chkFavorite.Text = "⭐ Guardar como favorito al ejecutar"
$chkFavorite.Font = $script:Fonts.Small
$chkFavorite.ForeColor = $script:Theme.TextBody
$chkFavorite.Location = New-Object System.Drawing.Point(750, 14)
$chkFavorite.Size = New-Object System.Drawing.Size(260, 22)
$chkFavorite.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$chkFavorite.Cursor = [System.Windows.Forms.Cursors]::Hand
$pnlAdvanced.Controls.Add($chkFavorite)

# Fila 2 Avanzado: Selector de Favoritos
$lblFavLabel = New-Object System.Windows.Forms.Label
$lblFavLabel.Text = "Favoritos:"
$lblFavLabel.Font = $script:Fonts.BodyBold
$lblFavLabel.ForeColor = $script:Theme.TextBody
$lblFavLabel.Location = New-Object System.Drawing.Point(20, 44)
$lblFavLabel.Size = New-Object System.Drawing.Size(75, 24)
$pnlAdvanced.Controls.Add($lblFavLabel)

$cboFavorites = New-Object System.Windows.Forms.ComboBox
$cboFavorites.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$cboFavorites.Font = $script:Fonts.Body
$cboFavorites.Location = New-Object System.Drawing.Point(95, 41)
$cboFavorites.Size = New-Object System.Drawing.Size(460, 26)
$cboFavorites.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left
$pnlAdvanced.Controls.Add($cboFavorites)

$btnLoadFav = New-Object System.Windows.Forms.Button
$btnLoadFav.Text = "Usar comando"
$btnLoadFav.Font = $script:Fonts.Small
$btnLoadFav.BackColor = $script:Theme.Secondary
$btnLoadFav.ForeColor = $script:Theme.SecondaryFg
$btnLoadFav.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLoadFav.FlatAppearance.BorderSize = 1
$btnLoadFav.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnLoadFav.Location = New-Object System.Drawing.Point(565, 40)
$btnLoadFav.Size = New-Object System.Drawing.Size(110, 27)
$btnLoadFav.Cursor = [System.Windows.Forms.Cursors]::Hand
$pnlAdvanced.Controls.Add($btnLoadFav)

$btnDeleteFav = New-Object System.Windows.Forms.Button
$btnDeleteFav.Text = "🗑️ Borrar favorito"
$btnDeleteFav.Font = $script:Fonts.Small
$btnDeleteFav.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FEE2E2')
$btnDeleteFav.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
$btnDeleteFav.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnDeleteFav.FlatAppearance.BorderSize = 0
$btnDeleteFav.Location = New-Object System.Drawing.Point(685, 40)
$btnDeleteFav.Size = New-Object System.Drawing.Size(125, 27)
$btnDeleteFav.Cursor = [System.Windows.Forms.Cursors]::Hand
$pnlAdvanced.Controls.Add($btnDeleteFav)

# Fila 3 Avanzado: TextBox de Comando y Boton Ejecutar
$lblCmdLabel = New-Object System.Windows.Forms.Label
$lblCmdLabel.Text = "adb:"
$lblCmdLabel.Font = New-Object System.Drawing.Font('Consolas', 11, [System.Drawing.FontStyle]::Bold)
$lblCmdLabel.ForeColor = $script:Theme.Primary
$lblCmdLabel.Location = New-Object System.Drawing.Point(20, 85)
$lblCmdLabel.Size = New-Object System.Drawing.Size(75, 28)
$pnlAdvanced.Controls.Add($lblCmdLabel)

$txtCustomCommand = New-Object System.Windows.Forms.TextBox
$txtCustomCommand.Font = $script:Fonts.Code
$txtCustomCommand.Text = "shell getprop ro.product.model"
$txtCustomCommand.Location = New-Object System.Drawing.Point(95, 82)
$txtCustomCommand.Size = New-Object System.Drawing.Size(840, 28)
$txtCustomCommand.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
$pnlAdvanced.Controls.Add($txtCustomCommand)

$btnExecuteCmd = New-Object System.Windows.Forms.Button
$btnExecuteCmd.Text = "▶ Ejecutar"
$btnExecuteCmd.Font = $script:Fonts.BodyBold
$btnExecuteCmd.BackColor = $script:Theme.Primary
$btnExecuteCmd.ForeColor = $script:Theme.PrimaryFg
$btnExecuteCmd.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExecuteCmd.FlatAppearance.BorderSize = 0
$btnExecuteCmd.Location = New-Object System.Drawing.Point(950, 80)
$btnExecuteCmd.Size = New-Object System.Drawing.Size(125, 32)
$btnExecuteCmd.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnExecuteCmd.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExecuteCmd.Add_MouseEnter({ $btnExecuteCmd.BackColor = $script:Theme.PrimaryHover })
$btnExecuteCmd.Add_MouseLeave({ $btnExecuteCmd.BackColor = $script:Theme.Primary })
$pnlAdvanced.Controls.Add($btnExecuteCmd)

$form.Controls.Add($pnlAdvanced)

# =============================================================================
# 2 Y 3. PANEL CENTRAL (Acciones a la Izquierda, Logs a la Derecha)
# =============================================================================
$pnlCenter = New-Object System.Windows.Forms.Panel
$pnlCenter.Dock = [System.Windows.Forms.DockStyle]::Fill
$pnlCenter.Padding = New-Object System.Windows.Forms.Padding(16, 14, 16, 14)
$pnlCenter.BackColor = $script:Theme.BgWindow

# -----------------------------------------------------------------------------
# PANEL IZQUIERDO - ACCIONES PRINCIPALES
# -----------------------------------------------------------------------------
$cardActions = New-Object System.Windows.Forms.Panel
$cardActions.Dock = [System.Windows.Forms.DockStyle]::Left
$cardActions.Width = 330
$cardActions.BackColor = $script:Theme.BgCard
$cardActions.Padding = New-Object System.Windows.Forms.Padding(14)
$cardActions.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle

$lblActionsHeader = New-Object System.Windows.Forms.Label
$lblActionsHeader.Text = "ACCIONES PRINCIPALES"
$lblActionsHeader.Font = $script:Fonts.TitleSmall
$lblActionsHeader.ForeColor = $script:Theme.TextMuted
$lblActionsHeader.Dock = [System.Windows.Forms.DockStyle]::Top
$lblActionsHeader.Height = 26
$cardActions.Controls.Add($lblActionsHeader)

$pnlActionButtons = New-Object System.Windows.Forms.FlowLayoutPanel
$pnlActionButtons.Dock = [System.Windows.Forms.DockStyle]::Fill
$pnlActionButtons.FlowDirection = [System.Windows.Forms.FlowDirection]::TopDown
$pnlActionButtons.WrapContents = $false
$pnlActionButtons.AutoScroll = $true
$pnlActionButtons.Padding = New-Object System.Windows.Forms.Padding(0, 4, 0, 0)

function New-ActionButton {
    param([string]$Icon, [string]$Title, [scriptblock]$OnClick)
    
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = " $Icon  $Title"
    $btn.Font = $script:Fonts.BodyBold
    $btn.Size = New-Object System.Drawing.Size(296, 44)
    $btn.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 8)
    $btn.BackColor = $script:Theme.BtnActionBg
    $btn.ForeColor = $script:Theme.BtnActionFg
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.FlatAppearance.BorderSize = 1
    $btn.FlatAppearance.BorderColor = $script:Theme.BtnActionBorder
    $btn.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
    $btn.Padding = New-Object System.Windows.Forms.Padding(12, 0, 0, 0)
    $btn.Cursor = [System.Windows.Forms.Cursors]::Hand
    
    $btn.Add_MouseEnter({
        param($sender, $eventArgs)
        $button = [System.Windows.Forms.Button]$sender
        $button.BackColor = $script:Theme.BtnActionHover
        $button.FlatAppearance.BorderColor = [System.Drawing.ColorTranslator]::FromHtml('#93C5FD')
        $button.ForeColor = $script:Theme.Primary
    })
    $btn.Add_MouseLeave({
        param($sender, $eventArgs)
        $button = [System.Windows.Forms.Button]$sender
        $button.BackColor = $script:Theme.BtnActionBg
        $button.FlatAppearance.BorderColor = $script:Theme.BtnActionBorder
        $button.ForeColor = $script:Theme.BtnActionFg
    })
    if ($OnClick) { $btn.Add_Click($OnClick) }
    return $btn
}

# Botones de Acciones Principales
$btnActionNewDev    = New-ActionButton -Icon "✨" -Title "Conectar Nuevo Dispositivo"
$btnActionDevices   = New-ActionButton -Icon "📱" -Title "Gestionar Dispositivos"
$btnActionTransfer  = New-ActionButton -Icon "⇄"  -Title "Transferir Archivos"
$btnActionExplorer  = New-ActionButton -Icon "📁" -Title "Explorador de Archivos"
$btnActionBackup    = New-ActionButton -Icon "💾" -Title "Backup Avanzado"
$btnActionApps      = New-ActionButton -Icon "📦" -Title "Gestion de Aplicaciones"
$btnActionScreen    = New-ActionButton -Icon "📸" -Title "Captura / Grabar Pantalla"
$btnActionClean     = New-ActionButton -Icon "🧹" -Title "Limpieza del Dispositivo"
$btnActionDiag      = New-ActionButton -Icon "🩺" -Title "Diagnostico Completo"

$pnlActionButtons.Controls.AddRange(@(
    $btnActionNewDev,
    $btnActionDevices,
    $btnActionTransfer,
    $btnActionExplorer,
    $btnActionBackup,
    $btnActionApps,
    $btnActionScreen,
    $btnActionClean,
    $btnActionDiag
))
$cardActions.Controls.Add($pnlActionButtons)
$pnlCenter.Controls.Add($cardActions)

# Separador entre paneles
$pnlSplitGap = New-Object System.Windows.Forms.Panel
$pnlSplitGap.Dock = [System.Windows.Forms.DockStyle]::Left
$pnlSplitGap.Width = 14
$pnlSplitGap.BackColor = $script:Theme.BgWindow
$pnlCenter.Controls.Add($pnlSplitGap)

# -----------------------------------------------------------------------------
# PANEL DERECHO - LOG Y ESTADO
# -----------------------------------------------------------------------------
$cardLog = New-Object System.Windows.Forms.Panel
$cardLog.Dock = [System.Windows.Forms.DockStyle]::Fill
$cardLog.BackColor = $script:Theme.BgCard
$cardLog.Padding = New-Object System.Windows.Forms.Padding(14)
$cardLog.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle

# Barra superior del panel de log
$pnlLogTop = New-Object System.Windows.Forms.Panel
$pnlLogTop.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlLogTop.Height = 36
$pnlLogTop.BackColor = $script:Theme.BgCard

$lblLogTitle = New-Object System.Windows.Forms.Label
$lblLogTitle.Text = "Registro de Actividad y Salida"
$lblLogTitle.Font = $script:Fonts.TitleMid
$lblLogTitle.ForeColor = $script:Theme.TextTitle
$lblLogTitle.Location = New-Object System.Drawing.Point(0, 6)
$lblLogTitle.Size = New-Object System.Drawing.Size(240, 24)
$pnlLogTop.Controls.Add($lblLogTitle)

$lblLastOp = New-Object System.Windows.Forms.Label
$lblLastOp.Text = "Ultima operacion: Ninguna"
$lblLastOp.Font = $script:Fonts.Small
$lblLastOp.ForeColor = $script:Theme.TextMuted
$lblLastOp.Location = New-Object System.Drawing.Point(245, 9)
$lblLastOp.Size = New-Object System.Drawing.Size(320, 20)
$pnlLogTop.Controls.Add($lblLastOp)

# Botones de utilidad para el log (derecha)
$btnLogClear = New-Object System.Windows.Forms.Button
$btnLogClear.Text = "🧹 Limpiar"
$btnLogClear.Font = $script:Fonts.Small
$btnLogClear.Size = New-Object System.Drawing.Size(80, 26)
$btnLogClear.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnLogClear.Location = New-Object System.Drawing.Point(440, 4)
$btnLogClear.BackColor = $script:Theme.Secondary
$btnLogClear.ForeColor = $script:Theme.SecondaryFg
$btnLogClear.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLogClear.FlatAppearance.BorderSize = 1
$btnLogClear.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnLogClear.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnLogClear.Add_Click({ $txtLog.Clear(); Write-LogEntry -Level 'INFO' -Message 'Visor de log limpiado.' })
$pnlLogTop.Controls.Add($btnLogClear)

$btnLogCopy = New-Object System.Windows.Forms.Button
$btnLogCopy.Text = "📋 Copiar"
$btnLogCopy.Font = $script:Fonts.Small
$btnLogCopy.Size = New-Object System.Drawing.Size(75, 26)
$btnLogCopy.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnLogCopy.Location = New-Object System.Drawing.Point(525, 4)
$btnLogCopy.BackColor = $script:Theme.Secondary
$btnLogCopy.ForeColor = $script:Theme.SecondaryFg
$btnLogCopy.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLogCopy.FlatAppearance.BorderSize = 1
$btnLogCopy.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnLogCopy.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnLogCopy.Add_Click({
    if (-not [string]::IsNullOrWhiteSpace($txtLog.Text)) {
        [System.Windows.Forms.Clipboard]::SetText($txtLog.Text)
        Write-LogEntry -Level 'INFO' -Message 'Log copiado al portapapeles.'
    }
})
$pnlLogTop.Controls.Add($btnLogCopy)

$btnLogFile = New-Object System.Windows.Forms.Button
$btnLogFile.Text = "📄 Ver archivo .log"
$btnLogFile.Font = $script:Fonts.Small
$btnLogFile.Size = New-Object System.Drawing.Size(120, 26)
$btnLogFile.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnLogFile.Location = New-Object System.Drawing.Point(605, 4)
$btnLogFile.BackColor = $script:Theme.Secondary
$btnLogFile.ForeColor = $script:Theme.SecondaryFg
$btnLogFile.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnLogFile.FlatAppearance.BorderSize = 1
$btnLogFile.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnLogFile.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnLogFile.Add_Click({
    try {
        $logPath = Get-AdbLogPath
        if (Test-Path -LiteralPath $logPath) {
            Start-Process -FilePath 'notepad.exe' -ArgumentList $logPath
        } else {
            [System.Windows.Forms.MessageBox]::Show("Aun no se ha creado el archivo de log para hoy:`n$logPath", "Log no encontrado", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Error al abrir log", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
    }
})
$pnlLogTop.Controls.Add($btnLogFile)

$cardLog.Controls.Add($pnlLogTop)

# Barra de progreso sutil (Marquee)
$pbProgress = New-Object System.Windows.Forms.ProgressBar
$pbProgress.Dock = [System.Windows.Forms.DockStyle]::Top
$pbProgress.Height = 3
$pbProgress.Visible = $false
$cardLog.Controls.Add($pbProgress)

# Visor RichTextBox estilo consola moderna
$txtLog = New-Object System.Windows.Forms.RichTextBox
$txtLog.Dock = [System.Windows.Forms.DockStyle]::Fill
$txtLog.BackColor = $script:Theme.LogBg
$txtLog.ForeColor = $script:Theme.LogFg
$txtLog.Font = $script:Fonts.Code
$txtLog.ReadOnly = $true
$txtLog.BorderStyle = [System.Windows.Forms.BorderStyle]::None
$txtLog.Margin = New-Object System.Windows.Forms.Padding(0, 8, 0, 0)
$cardLog.Controls.Add($txtLog)

$pnlCenter.Controls.Add($cardLog)
$form.Controls.Add($pnlCenter)

# =============================================================================
# LOGICA DE EVENTOS Y VINCULACION A LAS FUNCIONES DEL MODULO
# =============================================================================

# -----------------------------------------------------------------------------
# Cargar lista de favoritos en el ComboBox
# -----------------------------------------------------------------------------
function Load-FavoritesUi {
    $cboFavorites.Items.Clear()
    $commands = Get-AdbFavoriteCommands
    foreach ($item in $commands) {
        $display = if ($item.Name) { "$($item.Name) -> $($item.Command)" } else { $item.Command }
        $cboFavorites.Items.Add($display) | Out-Null
    }
    if ($cboFavorites.Items.Count -gt 0) {
        $cboFavorites.SelectedIndex = 0
    }
}

$btnLoadFav.Add_Click({
    if ($cboFavorites.SelectedIndex -ge 0) {
        $items = Get-AdbFavoriteCommands
        $selected = $items[$cboFavorites.SelectedIndex]
        if ($selected) {
            $txtCustomCommand.Text = $selected.Command
            Write-LogEntry -Level 'INFO' -Message "Comando favorito cargado: $($selected.Command)"
        }
    }
})

$btnDeleteFav.Add_Click({
    if ($cboFavorites.SelectedIndex -ge 0) {
        $items = @(Get-AdbFavoriteCommands)
        $idx = $cboFavorites.SelectedIndex
        $target = $items[$idx]
        $confirm = [System.Windows.Forms.MessageBox]::Show("¿Deseas eliminar el comando favorito:`n'$($target.Name)'?", "Confirmar eliminacion", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
            $updated = @($items | Where-Object { $_ -ne $target })
            Save-AdbFavoriteCommands -Commands $updated
            Load-FavoritesUi
            Write-LogEntry -Level 'INFO' -Message "Favorito eliminado."
        }
    }
})

# -----------------------------------------------------------------------------
# Ejecucion de comandos personalizados (Modo Avanzado)
# -----------------------------------------------------------------------------
$btnExecuteCmd.Add_Click({
    $cmdRaw = $txtCustomCommand.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($cmdRaw)) {
        [System.Windows.Forms.MessageBox]::Show("Por favor escribe un comando ADB para ejecutar.", "Comando vacio", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
        return
    }

    # Si se marco "Guardar como favorito", agregarlo
    if ($chkFavorite.Checked) {
        $favs = @(Get-AdbFavoriteCommands)
        $existing = @($favs | Where-Object { $_.Command -ceq $cmdRaw })
        if ($existing.Count -eq 0) {
            $newName = "Comando: " + ($cmdRaw -replace '\s+', ' ')
            if ($newName.Length -gt 28) { $newName = $newName.Substring(0, 25) + '...' }
            $favs += [pscustomobject]@{ Name = $newName; Command = $cmdRaw }
            Save-AdbFavoriteCommands -Commands $favs
            Load-FavoritesUi
            Write-LogEntry -Level 'SUCCESS' -Message "Comando guardado en favoritos."
        }
    }

    # Normalizar comando (remover 'adb ' al inicio si el usuario lo introdujo)
    $cleanCmd = $cmdRaw -replace '^adb\s+', ''

    # Ejecutar en segundo plano
    $worker = {
        param($cmdString)
        $dev = Get-CurrentAdbDevice
        $serial = if ($null -ne $dev -and $dev.Status -ceq 'device') { $dev.Serial } else { $null }

        # Comandos que no deben llevar -s (comandos de host)
        $isHostCmd = $cmdString -match '^(devices|version|start-server|kill-server|connect|disconnect|pair)\b'

        $argsList = @()
        if (-not $isHostCmd -and -not [string]::IsNullOrWhiteSpace($serial)) {
            $argsList += '-s'
            $argsList += $serial
        }

        # Separar argumentos respetando comillas
        # En PowerShell 5.1 podemos invocar adb directamente con ProcessStartInfo para control total
        $fullArgs = if ($argsList.Count -gt 0) { "$($argsList -join ' ') $cmdString" } else { $cmdString }
        
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'adb'
        $psi.Arguments = $fullArgs
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

        $proc = [System.Diagnostics.Process]::Start($psi)
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()

        return [pscustomobject]@{
            CommandExecuted = "adb $fullArgs"
            ExitCode = $proc.ExitCode
            StdOut = $stdout
            StdErr = $stderr
        }
    }

    Invoke-GuiAsyncTask -Title "adb $cleanCmd" -WorkerScript $worker -Parameters @{ cmdString = $cleanCmd } -OnCompleted {
        param($res)
        if ($res -and $res.Success -and $res.Output) {
            $cmdOut = $res.Output[0]
            Write-LogEntry -Level 'CMD' -Message "> $($cmdOut.CommandExecuted)"
            if (-not [string]::IsNullOrWhiteSpace($cmdOut.StdOut)) {
                $lines = $cmdOut.StdOut -split "`r?`n"
                foreach ($line in $lines) {
                    if (-not [string]::IsNullOrWhiteSpace($line)) {
                        Write-LogEntry -Level 'INFO' -Message $line
                    }
                }
            }
            if (-not [string]::IsNullOrWhiteSpace($cmdOut.StdErr)) {
                $errLines = $cmdOut.StdErr -split "`r?`n"
                foreach ($eline in $errLines) {
                    if (-not [string]::IsNullOrWhiteSpace($eline)) {
                        Write-LogEntry -Level 'WARN' -Message $eline
                    }
                }
            }
            if ($cmdOut.ExitCode -eq 0) {
                Write-LogEntry -Level 'SUCCESS' -Message "Ejecucion terminada con codigo 0."
            } else {
                Write-LogEntry -Level 'WARN' -Message "Ejecucion terminada con codigo de salida $($cmdOut.ExitCode)."
            }
        }
    }
})

# Permitir ejecutar con Enter en el TextBox de comandos
$txtCustomCommand.Add_KeyDown({
    if ($_.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        $_.SuppressKeyPress = $true
        $btnExecuteCmd.PerformClick()
    }
})

# =============================================================================
# ASISTENTE MODERNO PARA CONECTAR NUEVOS DISPOSITIVOS (DESDE CERO)
# =============================================================================
function Show-NewDeviceWizard {
    param([string]$InitialTab = 'USB')

    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Asistente de Conexion: Nuevo Dispositivo Android"
    $dlg.Size = New-Object System.Drawing.Size(690, 600)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false
    $dlg.MinimizeBox = $false

    # Banner superior
    $pnlBanner = New-Object System.Windows.Forms.Panel
    $pnlBanner.Dock = [System.Windows.Forms.DockStyle]::Top
    $pnlBanner.Height = 75
    $pnlBanner.BackColor = $script:Theme.BgCard
    $pnlBanner.Padding = New-Object System.Windows.Forms.Padding(16, 12, 16, 12)

    $lblBannerIcon = New-Object System.Windows.Forms.Label
    $lblBannerIcon.Text = "✨"
    $lblBannerIcon.Font = New-Object System.Drawing.Font('Segoe UI Emoji', 22)
    $lblBannerIcon.Location = New-Object System.Drawing.Point(14, 12)
    $lblBannerIcon.Size = New-Object System.Drawing.Size(46, 50)
    $pnlBanner.Controls.Add($lblBannerIcon)

    $lblBannerTitle = New-Object System.Windows.Forms.Label
    $lblBannerTitle.Text = "Conectar Nuevo Dispositivo (Desde Cero)"
    $lblBannerTitle.Font = $script:Fonts.TitleLarge
    $lblBannerTitle.ForeColor = $script:Theme.TextTitle
    $lblBannerTitle.Location = New-Object System.Drawing.Point(66, 14)
    $lblBannerTitle.Size = New-Object System.Drawing.Size(580, 26)
    $pnlBanner.Controls.Add($lblBannerTitle)

    $lblBannerSub = New-Object System.Windows.Forms.Label
    $lblBannerSub.Text = "Configura, autoriza y conecta tu telefono por Cable USB, WiFi o Pairing inalambrico"
    $lblBannerSub.Font = $script:Fonts.Small
    $lblBannerSub.ForeColor = $script:Theme.TextMuted
    $lblBannerSub.Location = New-Object System.Drawing.Point(66, 42)
    $lblBannerSub.Size = New-Object System.Drawing.Size(580, 20)
    $pnlBanner.Controls.Add($lblBannerSub)

    $pnlBannerBorder = New-Object System.Windows.Forms.Panel
    $pnlBannerBorder.Dock = [System.Windows.Forms.DockStyle]::Bottom
    $pnlBannerBorder.Height = 1
    $pnlBannerBorder.BackColor = $script:Theme.BorderCard
    $pnlBanner.Controls.Add($pnlBannerBorder)
    $dlg.Controls.Add($pnlBanner)

    # TabControl Principal
    $tabControl = New-Object System.Windows.Forms.TabControl
    $tabControl.Dock = [System.Windows.Forms.DockStyle]::Fill
    $tabControl.Font = $script:Fonts.BodyBold
    $tabControl.Padding = New-Object System.Drawing.Point(12, 8)

    # -------------------------------------------------------------------------
    # TAB 1: POR CABLE USB
    # -------------------------------------------------------------------------
    $tabUsb = New-Object System.Windows.Forms.TabPage
    $tabUsb.Text = " 🔌 Por Cable USB "
    $tabUsb.BackColor = $script:Theme.BgWindow
    $tabUsb.Padding = New-Object System.Windows.Forms.Padding(16)

    $lblUsbIntro = New-Object System.Windows.Forms.Label
    $lblUsbIntro.Text = "1. Conecta el cable USB del telefono a la PC.`n2. Asegurate de tener activada 'Depuracion por USB' en Ajustes > Opciones de desarrollador."
    $lblUsbIntro.Font = $script:Fonts.Body
    $lblUsbIntro.ForeColor = $script:Theme.TextBody
    $lblUsbIntro.Location = New-Object System.Drawing.Point(16, 14)
    $lblUsbIntro.Size = New-Object System.Drawing.Size(620, 42)
    $tabUsb.Controls.Add($lblUsbIntro)

    $btnDetectUsb = New-Object System.Windows.Forms.Button
    $btnDetectUsb.Text = "🔍 Buscar / Detectar Dispositivo USB"
    $btnDetectUsb.Font = $script:Fonts.BodyBold
    $btnDetectUsb.BackColor = $script:Theme.Primary
    $btnDetectUsb.ForeColor = $script:Theme.PrimaryFg
    $btnDetectUsb.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnDetectUsb.FlatAppearance.BorderSize = 0
    $btnDetectUsb.Location = New-Object System.Drawing.Point(16, 62)
    $btnDetectUsb.Size = New-Object System.Drawing.Size(280, 36)
    $btnDetectUsb.Cursor = [System.Windows.Forms.Cursors]::Hand
    $tabUsb.Controls.Add($btnDetectUsb)

    # Panel de Estado / Resultado USB
    $pnlUsbCard = New-Object System.Windows.Forms.Panel
    $pnlUsbCard.Location = New-Object System.Drawing.Point(16, 110)
    $pnlUsbCard.Size = New-Object System.Drawing.Size(635, 330)
    $pnlUsbCard.BackColor = $script:Theme.BgCard
    $pnlUsbCard.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $pnlUsbCard.Padding = New-Object System.Windows.Forms.Padding(14)
    $tabUsb.Controls.Add($pnlUsbCard)

    $lblUsbStatusTitle = New-Object System.Windows.Forms.Label
    $lblUsbStatusTitle.Text = "Estado: Esperando deteccion"
    $lblUsbStatusTitle.Font = $script:Fonts.TitleMid
    $lblUsbStatusTitle.ForeColor = $script:Theme.TextTitle
    $lblUsbStatusTitle.Location = New-Object System.Drawing.Point(14, 14)
    $lblUsbStatusTitle.Size = New-Object System.Drawing.Size(600, 24)
    $pnlUsbCard.Controls.Add($lblUsbStatusTitle)

    $txtUsbDetail = New-Object System.Windows.Forms.TextBox
    $txtUsbDetail.Multiline = $true
    $txtUsbDetail.ReadOnly = $true
    $txtUsbDetail.ScrollBars = [System.Windows.Forms.ScrollBars]::Vertical
    $txtUsbDetail.Font = $script:Fonts.Body
    $txtUsbDetail.BackColor = $script:Theme.BgCard
    $txtUsbDetail.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $txtUsbDetail.Location = New-Object System.Drawing.Point(14, 44)
    $txtUsbDetail.Size = New-Object System.Drawing.Size(600, 95)
    $txtUsbDetail.Text = "Presiona 'Buscar / Detectar Dispositivo USB' para comenzar.`r`nSi tu telefono ya esta conectado, se detectara su estado de autorizacion."
    $pnlUsbCard.Controls.Add($txtUsbDetail)

    $pnlUsbActions = New-Object System.Windows.Forms.Panel
    $pnlUsbActions.Location = New-Object System.Drawing.Point(14, 145)
    $pnlUsbActions.Size = New-Object System.Drawing.Size(605, 170)
    $pnlUsbCard.Controls.Add($pnlUsbActions)

    $fnUpdateUsbView = {
        param($deviceRecord)
        $pnlUsbActions.Controls.Clear()

        if ($null -eq $deviceRecord) {
            $pnlUsbCard.BackColor = $script:Theme.BgCard
            $txtUsbDetail.BackColor = $script:Theme.BgCard
            $lblUsbStatusTitle.Text = "❌ No se detecta ningun dispositivo USB"
            $lblUsbStatusTitle.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
            $txtUsbDetail.Text = "1. Conecta el cable USB a la PC y al telefono.`r`n2. Desbloquea la pantalla del telefono.`r`n3. En Android, cambia la notificacion de USB a 'Transferencia de archivos (MTP)'.`r`n4. Verifica que 'Depuracion por USB' este activada en Opciones de desarrollador."

            $btnRetryAdb = New-Object System.Windows.Forms.Button
            $btnRetryAdb.Text = "🔄 Reiniciar Servidor ADB y Reintentar"
            $btnRetryAdb.Font = $script:Fonts.BodyBold
            $btnRetryAdb.Size = New-Object System.Drawing.Size(280, 36)
            $btnRetryAdb.Location = New-Object System.Drawing.Point(0, 10)
            $btnRetryAdb.BackColor = $script:Theme.Secondary
            $btnRetryAdb.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
            $btnRetryAdb.Cursor = [System.Windows.Forms.Cursors]::Hand
            $btnRetryAdb.Add_Click({
                $txtUsbDetail.Text = "Reiniciando servidor ADB..."
                $dlg.Refresh()
                try {
                    Invoke-AdbHost -Arguments @('kill-server') | Out-Null
                    Start-Sleep -Milliseconds 600
                    Invoke-AdbHost -Arguments @('start-server') | Out-Null
                    Start-Sleep -Milliseconds 600
                } catch { }
                $btnDetectUsb.PerformClick()
            })
            $pnlUsbActions.Controls.Add($btnRetryAdb)
        }
        elseif ($deviceRecord.Status -ceq 'unauthorized') {
            $pnlUsbCard.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFBEB')
            $txtUsbDetail.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFBEB')
            $lblUsbStatusTitle.Text = "⚠️ DISPOSITIVO DETECTADO PERO FALTA AUTORIZACION"
            $lblUsbStatusTitle.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#B45309')
            $txtUsbDetail.Text = "Serial: $($deviceRecord.Serial)`r`n`r`n¡ATENCION! Mira la pantalla de tu telefono Android ahora mismo.`r`nAparecera una ventana emergente que dice:`r`n'¿Permitir depuracion por USB desde esta computadora?'`r`n`r`n👉 MARCA la casilla 'Permitir siempre desde este equipo' y presiona 'PERMITIR'."

            $btnCheckAuth = New-Object System.Windows.Forms.Button
            $btnCheckAuth.Text = "✅ Ya acepte en el telefono (Verificar)"
            $btnCheckAuth.Font = $script:Fonts.BodyBold
            $btnCheckAuth.Size = New-Object System.Drawing.Size(280, 36)
            $btnCheckAuth.Location = New-Object System.Drawing.Point(0, 10)
            $btnCheckAuth.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
            $btnCheckAuth.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
            $btnCheckAuth.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
            $btnCheckAuth.Cursor = [System.Windows.Forms.Cursors]::Hand
            $btnCheckAuth.Add_Click({ $btnDetectUsb.PerformClick() })
            $pnlUsbActions.Controls.Add($btnCheckAuth)

            $btnForcePrompt = New-Object System.Windows.Forms.Button
            $btnForcePrompt.Text = "⚡ Forzar ventana de permiso (Reiniciar ADB)"
            $btnForcePrompt.Font = $script:Fonts.Small
            $btnForcePrompt.Size = New-Object System.Drawing.Size(290, 36)
            $btnForcePrompt.Location = New-Object System.Drawing.Point(295, 10)
            $btnForcePrompt.BackColor = $script:Theme.Secondary
            $btnForcePrompt.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
            $btnForcePrompt.Cursor = [System.Windows.Forms.Cursors]::Hand
            $btnForcePrompt.Add_Click({
                $txtUsbDetail.Text = "Reiniciando servidor ADB para solicitar la clave RSA en pantalla..."
                $dlg.Refresh()
                try {
                    Invoke-AdbHost -Arguments @('kill-server') | Out-Null
                    Start-Sleep -Milliseconds 800
                    Invoke-AdbHost -Arguments @('start-server') | Out-Null
                    Start-Sleep -Milliseconds 800
                } catch { }
                $btnDetectUsb.PerformClick()
            })
            $pnlUsbActions.Controls.Add($btnForcePrompt)

            $btnGoAuthTab = New-Object System.Windows.Forms.Button
            $btnGoAuthTab.Text = "🛠️ Ver guia detallada si no aparece el mensaje"
            $btnGoAuthTab.Font = $script:Fonts.Small
            $btnGoAuthTab.Size = New-Object System.Drawing.Size(320, 30)
            $btnGoAuthTab.Location = New-Object System.Drawing.Point(0, 56)
            $btnGoAuthTab.BackColor = $script:Theme.BgCard
            $btnGoAuthTab.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
            $btnGoAuthTab.Cursor = [System.Windows.Forms.Cursors]::Hand
            $btnGoAuthTab.Add_Click({ $tabControl.SelectedTab = $tabAuth })
            $pnlUsbActions.Controls.Add($btnGoAuthTab)
        }
        elseif ($deviceRecord.Status -ceq 'device') {
            $pnlUsbCard.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#F0FDF4')
            $txtUsbDetail.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#F0FDF4')
            $lblUsbStatusTitle.Text = "✅ ¡Dispositivo Autorizado y Conectado!"
            $lblUsbStatusTitle.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#15803D')

            $modelName = $deviceRecord.Serial
            try {
                $mOut = @(Invoke-AdbCommand -Serial $deviceRecord.Serial -Arguments @('shell','getprop','ro.product.model'))
                $detectedModel = ($mOut -join ' ').Trim()
                if (-not [string]::IsNullOrWhiteSpace($detectedModel)) { $modelName = $detectedModel }
            } catch { }

            $txtUsbDetail.Text = "Modelo: $modelName | Serial: $($deviceRecord.Serial)`r`nEstado: device (Totalmente autorizado para depuracion y transferencias)."

            $lblSaveName = New-Object System.Windows.Forms.Label
            $lblSaveName.Text = "Nombre para recordar este telefono:"
            $lblSaveName.Font = $script:Fonts.Small
            $lblSaveName.ForeColor = $script:Theme.TextBody
            $lblSaveName.Location = New-Object System.Drawing.Point(0, 6)
            $lblSaveName.Size = New-Object System.Drawing.Size(240, 18)
            $pnlUsbActions.Controls.Add($lblSaveName)

            $txtFriendly = New-Object System.Windows.Forms.TextBox
            $txtFriendly.Text = $modelName
            $txtFriendly.Font = $script:Fonts.Body
            $txtFriendly.Location = New-Object System.Drawing.Point(0, 26)
            $txtFriendly.Size = New-Object System.Drawing.Size(320, 26)
            $pnlUsbActions.Controls.Add($txtFriendly)

            $btnSaveUsb = New-Object System.Windows.Forms.Button
            $btnSaveUsb.Text = "💾 Guardar USB (usar ahora)"
            $btnSaveUsb.Font = $script:Fonts.BodyBold
            $btnSaveUsb.Size = New-Object System.Drawing.Size(200, 32)
            $btnSaveUsb.Location = New-Object System.Drawing.Point(330, 24)
            $btnSaveUsb.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
            $btnSaveUsb.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
            $btnSaveUsb.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
            $btnSaveUsb.Cursor = [System.Windows.Forms.Cursors]::Hand
            $btnSaveUsb.Add_Click({
                $fName = $txtFriendly.Text.Trim()
                if ([string]::IsNullOrWhiteSpace($fName)) { $fName = $modelName }
                if ([string]::IsNullOrWhiteSpace($fName)) { $fName = $deviceRecord.Serial }
                try {
                    # USB no es un endpoint host:puerto. Se conserva en UsbSerial
                    # y se selecciona usando el serial detectado en esta sesión.
                    $saved = Save-GuiAdbDevice -FriendlyName $fName -Mode 'usb' -UsbSerial $deviceRecord.Serial -IsPaired $true
                    $selected = [pscustomobject]@{ Id=$saved.Id; FriendlyName=$saved.FriendlyName; Serial=$deviceRecord.Serial; UsbSerial=$deviceRecord.Serial; Mode='usb'; LastEndpoint=$null }
                    Set-CurrentAdbDevice -Device $selected -Persist | Out-Null
                    Write-LogEntry -Level 'SUCCESS' -Message "Dispositivo USB '$fName' guardado y seleccionado como activo."
                    $dlg.Close()
                    Refresh-DeviceStatus
                } catch {
                    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Error al guardar", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
                }
            })
            $pnlUsbActions.Controls.Add($btnSaveUsb)

            $lblWifiHint = New-Object System.Windows.Forms.Label
            $lblWifiHint.Text = "Opcion Inalambrica: ¿Deseas desconectar el cable y usarlo por WiFi?"
            $lblWifiHint.Font = $script:Fonts.Small
            $lblWifiHint.ForeColor = $script:Theme.TextMuted
            $lblWifiHint.Location = New-Object System.Drawing.Point(0, 70)
            $lblWifiHint.Size = New-Object System.Drawing.Size(500, 18)
            $pnlUsbActions.Controls.Add($lblWifiHint)

            $btnSwitchWifi = New-Object System.Windows.Forms.Button
            $btnSwitchWifi.Text = "📶 Activar Modo WiFi (TCP/IP 5555) y Conectar"
            $btnSwitchWifi.Font = $script:Fonts.BodyBold
            $btnSwitchWifi.Size = New-Object System.Drawing.Size(340, 36)
            $btnSwitchWifi.Location = New-Object System.Drawing.Point(0, 92)
            $btnSwitchWifi.BackColor = $script:Theme.Primary
            $btnSwitchWifi.ForeColor = $script:Theme.PrimaryFg
            $btnSwitchWifi.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
            $btnSwitchWifi.Cursor = [System.Windows.Forms.Cursors]::Hand
            $btnSwitchWifi.Add_Click({
                $btnSwitchWifi.Enabled = $false
                $btnSwitchWifi.Text = "Activando WiFi en el telefono..."
                $dlg.Refresh()
                try {
                    # Leer la IP mientras el canal USB sigue estable. Después de
                    # adb tcpip Android puede reiniciar la conexión USB.
                    $ip = Get-AdbDeviceIpv4 -Serial $deviceRecord.Serial
                    if ([string]::IsNullOrWhiteSpace($ip)) {
                        $ip = [Microsoft.VisualBasic.Interaction]::InputBox("No se pudo auto-detectar la IP. Introduce la IP del telefono en la red WiFi:", "IP de Android", "192.168.1.")
                    }

                    if (-not [string]::IsNullOrWhiteSpace($ip)) {
                        Invoke-Adb -Serial $deviceRecord.Serial -Arguments @('tcpip','5555') | Out-Null
                        Start-Sleep -Seconds 1
                        $endpoint = "$ip`:5555"
                        $conn = Connect-AdbEndpoint -Endpoint $endpoint -TimeoutSeconds 15
                        $fName = $txtFriendly.Text.Trim()
                        if ([string]::IsNullOrWhiteSpace($fName)) { $fName = $modelName }
                        if ([string]::IsNullOrWhiteSpace($fName)) { $fName = $deviceRecord.Serial }
                        $saved = Save-GuiAdbDevice -FriendlyName "$fName (WiFi)" -Mode 'tcpip-5555' -UsbSerial $deviceRecord.Serial -LastEndpoint $conn.Serial -IsPaired $false
                        Set-CurrentAdbDevice -Device $saved -Persist | Out-Null
                        Write-LogEntry -Level 'SUCCESS' -Message "¡Conexion WiFi exitosa con $endpoint! Ya puedes desconectar el cable USB."
                        [System.Windows.Forms.MessageBox]::Show("¡Telefono conectado por WiFi en $endpoint!`n`nYa puedes desconectar el cable USB.", "Modo Inalambrico Listo", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
                        $dlg.Close()
                        Refresh-DeviceStatus
                    }
                } catch {
                    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Error al activar WiFi", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
                    $btnSwitchWifi.Enabled = $true
                    $btnSwitchWifi.Text = "📶 Activar Modo WiFi (TCP/IP 5555) y Conectar"
                }
            })
            $pnlUsbActions.Controls.Add($btnSwitchWifi)
        }
    }

    $btnDetectUsb.Add_Click({
        $btnDetectUsb.Enabled = $false
        $lblUsbStatusTitle.Text = "Buscando dispositivos USB..."
        $dlg.Refresh()

        $devs = @()
        try { $devs = @(Get-AdbDevices) } catch { }
        
        $usbMatch = @($devs | Where-Object { $_.Serial -notmatch ':\d+$' })[0]
        if ($null -eq $usbMatch -and $devs.Count -gt 0) { $usbMatch = $devs[0] }

        if ($null -ne $usbMatch -and $usbMatch.Status -ceq 'device') {
            # La detección USB ya es suficiente para ejecutar comandos por cable;
            # no obligamos al usuario a guardar el dispositivo para seleccionarlo.
            try {
                Set-CurrentAdbDevice -Device ([pscustomobject]@{ Serial=$usbMatch.Serial }) | Out-Null
                Write-LogEntry -Level 'SUCCESS' -Message "USB detectado y seleccionado como activo: $($usbMatch.Serial)."
            } catch {
                Write-LogEntry -Level 'WARN' -Message "USB detectado, pero no se pudo seleccionar como activo: $($_.Exception.Message)"
            }
        }

        & $fnUpdateUsbView $usbMatch
        $btnDetectUsb.Enabled = $true
    })

    $tabControl.TabPages.Add($tabUsb)

    # -------------------------------------------------------------------------
    # TAB 2: WIRELESS PAIRING (ANDROID 11+)
    # -------------------------------------------------------------------------
    $tabPair = New-Object System.Windows.Forms.TabPage
    $tabPair.Text = " 📶 WiFi Inalambrico (Pairing) "
    $tabPair.BackColor = $script:Theme.BgWindow
    $tabPair.Padding = New-Object System.Windows.Forms.Padding(16)

    $lblPairIntro = New-Object System.Windows.Forms.Label
    $lblPairIntro.Text = "Para Android 11+ sin necesidad de cable:`n1. Conecta PC y telefono a la misma red WiFi.`n2. En el telefono: Ajustes > Opciones de desarrollador > Depuracion inalambrica.`n3. Toca en 'Vincular dispositivo con codigo de sincronizacion'."
    $lblPairIntro.Font = $script:Fonts.Body
    $lblPairIntro.ForeColor = $script:Theme.TextBody
    $lblPairIntro.Location = New-Object System.Drawing.Point(16, 14)
    $lblPairIntro.Size = New-Object System.Drawing.Size(620, 68)
    $tabPair.Controls.Add($lblPairIntro)

    $pnlPairForm = New-Object System.Windows.Forms.Panel
    $pnlPairForm.Location = New-Object System.Drawing.Point(16, 90)
    $pnlPairForm.Size = New-Object System.Drawing.Size(635, 350)
    $pnlPairForm.BackColor = $script:Theme.BgCard
    $pnlPairForm.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $pnlPairForm.Padding = New-Object System.Windows.Forms.Padding(16)
    $tabPair.Controls.Add($pnlPairForm)

    function Add-FormField {
        param($Parent, [string]$LabelText, [int]$Top, [string]$DefaultVal = '')
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text = $LabelText
        $lbl.Font = $script:Fonts.Small
        $lbl.ForeColor = $script:Theme.TextBody
        $lbl.Location = New-Object System.Drawing.Point(16, $Top)
        $lbl.Size = New-Object System.Drawing.Size(260, 20)
        $Parent.Controls.Add($lbl)

        $txt = New-Object System.Windows.Forms.TextBox
        $txt.Text = $DefaultVal
        $txt.Font = $script:Fonts.Body
        $txt.Location = New-Object System.Drawing.Point(280, ($Top - 2))
        $txt.Size = New-Object System.Drawing.Size(320, 26)
        $Parent.Controls.Add($txt)
        return $txt
    }

    $txtPairName     = Add-FormField -Parent $pnlPairForm -LabelText "Nombre del telefono (ej. Mi Galaxy):" -Top 16 -DefaultVal "Telefono WiFi"
    $txtPairHost     = Add-FormField -Parent $pnlPairForm -LabelText "IP mostrada en pantalla (ej. 192.168.1.50):" -Top 56 -DefaultVal "192.168.1."
    $txtPairPort     = Add-FormField -Parent $pnlPairForm -LabelText "Puerto de vinculacion (Pairing port):" -Top 96 -DefaultVal ""
    $txtPairCode     = Add-FormField -Parent $pnlPairForm -LabelText "Codigo de 6 digitos (Pairing code):" -Top 136 -DefaultVal ""
    $txtPairConnPort = Add-FormField -Parent $pnlPairForm -LabelText "Puerto de conexion (Mostrado en pantalla):" -Top 176 -DefaultVal ""

    $lblPairMsg = New-Object System.Windows.Forms.Label
    $lblPairMsg.Text = ""
    $lblPairMsg.Font = $script:Fonts.Small
    $lblPairMsg.Location = New-Object System.Drawing.Point(16, 215)
    $lblPairMsg.Size = New-Object System.Drawing.Size(585, 38)
    $pnlPairForm.Controls.Add($lblPairMsg)

    $btnDoPair = New-Object System.Windows.Forms.Button
    $btnDoPair.Text = "🔗 Vincular y Conectar Telefono"
    $btnDoPair.Font = $script:Fonts.BodyBold
    $btnDoPair.Size = New-Object System.Drawing.Size(280, 40)
    $btnDoPair.Location = New-Object System.Drawing.Point(280, 260)
    $btnDoPair.BackColor = $script:Theme.Primary
    $btnDoPair.ForeColor = $script:Theme.PrimaryFg
    $btnDoPair.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnDoPair.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnDoPair.Add_Click({
        $hostIp = $txtPairHost.Text.Trim()
        $pPort = $txtPairPort.Text.Trim()
        $code = $txtPairCode.Text.Trim()
        $cPort = $txtPairConnPort.Text.Trim()
        $fName = $txtPairName.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($fName)) { $fName = "Android WiFi" }

        if ([string]::IsNullOrWhiteSpace($hostIp) -or [string]::IsNullOrWhiteSpace($pPort) -or [string]::IsNullOrWhiteSpace($code)) {
            $lblPairMsg.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
            $lblPairMsg.Text = "Por favor completa la IP, el puerto de pairing y el codigo de 6 digitos."
            return
        }

        $btnDoPair.Enabled = $false
        $lblPairMsg.ForeColor = $script:Theme.Primary
        $lblPairMsg.Text = "Ejecutando vinculacion (adb pair $hostIp`:$pPort $code)..."
        $dlg.Refresh()

        try {
            $pairEndpoint = if ($hostIp.Contains(':')) { "[$hostIp]:$pPort" } else { "$hostIp`:$pPort" }
            $pairOutput = @(Invoke-AdbHost -Arguments @('pair', $pairEndpoint, $code))
            $pairStr = $pairOutput -join ' '
            if ($pairStr -match '(?i)failed|error') {
                throw "Fallo el emparejamiento: $pairStr"
            }

            if ([string]::IsNullOrWhiteSpace($cPort)) {
                $mdns = Get-AdbMdnsConnectionEndpoint -PreferredHost $hostIp
                if ($mdns) { $cPort = $mdns.Port } else { $cPort = $pPort }
            }

            $connEndpoint = if ($hostIp.Contains(':')) { "[$hostIp]:$cPort" } else { "$hostIp`:$cPort" }
            $lblPairMsg.Text = "Pairing exitoso. Conectando a $connEndpoint..."
            $dlg.Refresh()

            $record = Connect-AdbEndpoint -Endpoint $connEndpoint -TimeoutSeconds 15
            $saved = Add-SavedAdbDevice -FriendlyName $fName -Mode 'wireless-debugging-tls' -LastEndpoint $record.Serial -IsPaired $true -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
            Set-CurrentAdbDevice -Device $saved -Persist | Out-Null

            Write-LogEntry -Level 'SUCCESS' -Message "Dispositivo inalambrico '$fName' vinculado y conectado exitosamente."
            [System.Windows.Forms.MessageBox]::Show("¡Dispositivo vinculado y conectado correctamente!", "Exito", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
            $dlg.Close()
            Refresh-DeviceStatus
        } catch {
            $lblPairMsg.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
            $lblPairMsg.Text = "Error: $($_.Exception.Message)"
            $btnDoPair.Enabled = $true
        }
    })
    $pnlPairForm.Controls.Add($btnDoPair)

    $tabControl.TabPages.Add($tabPair)

    # -------------------------------------------------------------------------
    # TAB 3: CONEXION DIRECTA IP
    # -------------------------------------------------------------------------
    $tabDirect = New-Object System.Windows.Forms.TabPage
    $tabDirect.Text = " 🌐 Conexion Directa IP "
    $tabDirect.BackColor = $script:Theme.BgWindow
    $tabDirect.Padding = New-Object System.Windows.Forms.Padding(16)

    $lblDirectIntro = New-Object System.Windows.Forms.Label
    $lblDirectIntro.Text = "Conecta a un telefono que ya tiene el puerto TCP/IP 5555 habilitado o una IP conocida."
    $lblDirectIntro.Font = $script:Fonts.Body
    $lblDirectIntro.ForeColor = $script:Theme.TextBody
    $lblDirectIntro.Location = New-Object System.Drawing.Point(16, 14)
    $lblDirectIntro.Size = New-Object System.Drawing.Size(620, 24)
    $tabDirect.Controls.Add($lblDirectIntro)

    $pnlDirectForm = New-Object System.Windows.Forms.Panel
    $pnlDirectForm.Location = New-Object System.Drawing.Point(16, 50)
    $pnlDirectForm.Size = New-Object System.Drawing.Size(635, 260)
    $pnlDirectForm.BackColor = $script:Theme.BgCard
    $pnlDirectForm.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $pnlDirectForm.Padding = New-Object System.Windows.Forms.Padding(16)
    $tabDirect.Controls.Add($pnlDirectForm)

    $txtDirectEp   = Add-FormField -Parent $pnlDirectForm -LabelText "IP y Puerto (ej. 192.168.1.50:5555):" -Top 24 -DefaultVal "192.168.1."
    $txtDirectName = Add-FormField -Parent $pnlDirectForm -LabelText "Nombre para guardar (opcional):" -Top 70 -DefaultVal ""

    $lblDirectMsg = New-Object System.Windows.Forms.Label
    $lblDirectMsg.Text = ""
    $lblDirectMsg.Font = $script:Fonts.Small
    $lblDirectMsg.Location = New-Object System.Drawing.Point(16, 120)
    $lblDirectMsg.Size = New-Object System.Drawing.Size(585, 30)
    $pnlDirectForm.Controls.Add($lblDirectMsg)

    $btnDirectConnect = New-Object System.Windows.Forms.Button
    $btnDirectConnect.Text = "⚡ Conectar por IP"
    $btnDirectConnect.Font = $script:Fonts.BodyBold
    $btnDirectConnect.Size = New-Object System.Drawing.Size(200, 38)
    $btnDirectConnect.Location = New-Object System.Drawing.Point(280, 160)
    $btnDirectConnect.BackColor = $script:Theme.Primary
    $btnDirectConnect.ForeColor = $script:Theme.PrimaryFg
    $btnDirectConnect.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnDirectConnect.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnDirectConnect.Add_Click({
        $ep = $txtDirectEp.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($ep) -or $ep -eq '192.168.1.') { return }
        $fName = $txtDirectName.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($fName)) { $fName = $ep }

        $btnDirectConnect.Enabled = $false
        $lblDirectMsg.ForeColor = $script:Theme.Primary
        $lblDirectMsg.Text = "Conectando a $ep..."
        $dlg.Refresh()

        try {
            $record = Connect-AdbEndpoint -Endpoint $ep -TimeoutSeconds 15
            if ($record.Status -ceq 'unauthorized') {
                $lblDirectMsg.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#B45309')
                $lblDirectMsg.Text = "Conectado pero NO AUTORIZADO. Ve a la pestana Reparar Autorizacion."
                $tabControl.SelectedTab = $tabAuth
            } else {
                $saved = Add-SavedAdbDevice -FriendlyName $fName -Mode 'tcpip-5555' -LastEndpoint $record.Serial -IsPaired $true -LastConnectedUtc ([DateTime]::UtcNow.ToString('o'))
                Set-CurrentAdbDevice -Device $saved -Persist | Out-Null
                Write-LogEntry -Level 'SUCCESS' -Message "Conectado exitosamente a $ep."
                $dlg.Close()
                Refresh-DeviceStatus
            }
        } catch {
            $lblDirectMsg.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
            $lblDirectMsg.Text = "Error: $($_.Exception.Message)"
        } finally {
            $btnDirectConnect.Enabled = $true
        }
    })
    $pnlDirectForm.Controls.Add($btnDirectConnect)

    $tabControl.TabPages.Add($tabDirect)

    # -------------------------------------------------------------------------
    # TAB 4: REPARAR AUTORIZACION / PERMISOS
    # -------------------------------------------------------------------------
    $tabAuth = New-Object System.Windows.Forms.TabPage
    $tabAuth.Text = " 🛠️ Reparar Autorizacion "
    $tabAuth.BackColor = $script:Theme.BgWindow
    $tabAuth.Padding = New-Object System.Windows.Forms.Padding(16)

    $lblAuthTitle = New-Object System.Windows.Forms.Label
    $lblAuthTitle.Text = "¿Tu telefono esta conectado pero dice 'unauthorized' o no aparece la ventana de permiso?"
    $lblAuthTitle.Font = $script:Fonts.TitleSmall
    $lblAuthTitle.ForeColor = $script:Theme.TextTitle
    $lblAuthTitle.Location = New-Object System.Drawing.Point(16, 12)
    $lblAuthTitle.Size = New-Object System.Drawing.Size(620, 24)
    $tabAuth.Controls.Add($lblAuthTitle)

    $pnlAuthActions = New-Object System.Windows.Forms.FlowLayoutPanel
    $pnlAuthActions.Location = New-Object System.Drawing.Point(16, 40)
    $pnlAuthActions.Size = New-Object System.Drawing.Size(635, 42)
    $pnlAuthActions.FlowDirection = [System.Windows.Forms.FlowDirection]::LeftToRight

    $btnFixKillStart = New-Object System.Windows.Forms.Button
    $btnFixKillStart.Text = "🔄 Reiniciar Servidor ADB (kill & start)"
    $btnFixKillStart.Font = $script:Fonts.BodyBold
    $btnFixKillStart.Size = New-Object System.Drawing.Size(290, 36)
    $btnFixKillStart.BackColor = $script:Theme.Secondary
    $btnFixKillStart.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnFixKillStart.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnFixKillStart.Add_Click({
        Write-LogEntry -Level 'INFO' -Message "Reiniciando servidor ADB..."
        try {
            Invoke-AdbHost -Arguments @('kill-server') | Out-Null
            Start-Sleep -Milliseconds 800
            Invoke-AdbHost -Arguments @('start-server') | Out-Null
            Start-Sleep -Milliseconds 800
            $devs = @(Get-AdbDevices)
            [System.Windows.Forms.MessageBox]::Show("Servidor ADB reiniciado.`nDispositivos detectados: $($devs.Count)`n`nMira la pantalla de tu telefono para aceptar la autorizacion.", "ADB Reiniciado", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
            Refresh-DeviceStatus -Silent
        } catch {
            [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Error al reiniciar ADB", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
        }
    })
    $pnlAuthActions.Controls.Add($btnFixKillStart)

    $btnFixQuery = New-Object System.Windows.Forms.Button
    $btnFixQuery.Text = "🔑 Comprobar Estado (adb devices)"
    $btnFixQuery.Font = $script:Fonts.BodyBold
    $btnFixQuery.Size = New-Object System.Drawing.Size(260, 36)
    $btnFixQuery.BackColor = $script:Theme.Secondary
    $btnFixQuery.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnFixQuery.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnFixQuery.Add_Click({
        $devs = @(Get-AdbDevices)
        if ($devs.Count -gt 0) {
            $summary = ($devs | ForEach-Object { "$($_.Serial) -> $($_.Status)" }) -join "`n"
            [System.Windows.Forms.MessageBox]::Show("Dispositivos:`n$summary", "Estado ADB", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
        } else {
            [System.Windows.Forms.MessageBox]::Show("No hay ningun dispositivo detectado.", "Estado ADB", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
        }
        Refresh-DeviceStatus -Silent
    })
    $pnlAuthActions.Controls.Add($btnFixQuery)
    $tabAuth.Controls.Add($pnlAuthActions)

    $pnlAuthGuide = New-Object System.Windows.Forms.Panel
    $pnlAuthGuide.Location = New-Object System.Drawing.Point(16, 92)
    $pnlAuthGuide.Size = New-Object System.Drawing.Size(635, 350)
    $pnlAuthGuide.BackColor = $script:Theme.BgCard
    $pnlAuthGuide.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $pnlAuthGuide.Padding = New-Object System.Windows.Forms.Padding(16)

    $txtAuthGuide = New-Object System.Windows.Forms.TextBox
    $txtAuthGuide.Multiline = $true
    $txtAuthGuide.ReadOnly = $true
    $txtAuthGuide.ScrollBars = [System.Windows.Forms.ScrollBars]::Vertical
    $txtAuthGuide.Font = $script:Fonts.Body
    $txtAuthGuide.BackColor = $script:Theme.BgCard
    $txtAuthGuide.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $txtAuthGuide.Dock = [System.Windows.Forms.DockStyle]::Fill
    $txtAuthGuide.Text = @"
GUIA PASO A PASO PARA SOLUCIONAR EL PERMISO DE DEPURACION:

1. REVOCAR AUTORIZACIONES PREVIAS (Muy efectivo)
   • En tu Android, abre: Ajustes > Opciones de desarrollador.
   • Busca la opcion: 'Revocar autorizaciones de depuracion por USB'.
   • Pulsa en Aceptar.
   • Desconecta el cable USB de la PC, espera 3 segundos y vuelvelo a conectar.
   • Aparecera de inmediato la ventana en pantalla para autorizar.

2. VERIFICAR MODO DE CONEXION USB
   • Al conectar el cable, desliza hacia abajo la barra de notificaciones de Android.
   • Toca en la notificacion del sistema 'Cargando por USB' o 'Sistema Android'.
   • Cambia de 'Solo carga' a 'Transferencia de archivos' o 'MTP'.

3. PANTALLA BLOQUEADA
   • Android no muestra la solicitud de depuracion si la pantalla esta apagada o con el bloqueo de PIN/Huella activo. Desbloquea tu pantalla.

4. CABLE DE DATOS INCOMPATIBLE
   • Muchos cables comerciales solo tienen cables de alimentacion (2 pines) y carecen de lineas de datos (D+ / D-). Prueba con el cable original de tu telefono.

5. CONEXION POR RED WIFI
   • Si el telefono cambio de direccion IP al reconectarse al router, es necesario emparejarlo con el nuevo puerto usando la pestana 'WiFi Inalambrico (Pairing)'.
"@
    $pnlAuthGuide.Controls.Add($txtAuthGuide)
    $tabAuth.Controls.Add($pnlAuthGuide)

    $tabControl.TabPages.Add($tabAuth)
    $dlg.Controls.Add($tabControl)

    if ($InitialTab -eq 'Auth') {
        $tabControl.SelectedTab = $tabAuth
    } elseif ($InitialTab -eq 'Pair') {
        $tabControl.SelectedTab = $tabPair
    } elseif ($InitialTab -eq 'Direct') {
        $tabControl.SelectedTab = $tabDirect
    } else {
        $tabControl.SelectedTab = $tabUsb
        $dlg.Add_Shown({ $btnDetectUsb.PerformClick() })
    }

    $null = $dlg.ShowDialog($form)
}

# -----------------------------------------------------------------------------
# VINCULACION DE EVENTOS DE CABECERA Y ASISTENTE
# -----------------------------------------------------------------------------
$btnConnectDevice.Add_Click({ Show-NewDeviceWizard })
$btnActionNewDev.Add_Click({ Show-NewDeviceWizard })

$btnRefresh.Add_Click({ Refresh-DeviceStatus })

$btnReconnect.Add_Click({
    Write-LogEntry -Level 'INFO' -Message 'Iniciando reconexion inteligente de dispositivo...'
    
    $worker = {
        $cur = Get-CurrentAdbDevice
        $ready = $false
        if ($null -ne $cur) {
            $ready = Test-AdbDeviceReady -Device $cur -AutoReconnect $true
        } else {
            # Si no hay actual, intentar con los guardados
            $saved = @(Get-SavedAdbDevices)
            foreach ($dev in $saved) {
                if (Test-AdbDeviceReady -Device $dev -AutoReconnect $true) {
                    $ready = $true
                    break
                }
            }
        }
        return $ready
    }

    Invoke-GuiAsyncTask -Title 'Reconexion inteligente' -WorkerScript $worker -OnCompleted {
        param($res)
        if ($res -and $res.Success -and $res.Output[0] -eq $true) {
            Write-LogEntry -Level 'SUCCESS' -Message 'Reconexion completada y verificada exitosamente.'
        } else {
            Write-LogEntry -Level 'WARN' -Message 'No se pudo reconectar automaticamente. Revisa la conexion USB o la IP WiFi.'
        }
        Refresh-DeviceStatus -Silent
    }
})

# =============================================================================
# IMPLEMENTACION DE ACCIONES PRINCIPALES
# =============================================================================

# 1. GESTIONAR DISPOSITIVOS
$btnActionDevices.Add_Click({
    # Crear formulario modal elegante de seleccion de dispositivos
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Gestionar Dispositivos ADB"
    $dlg.Size = New-Object System.Drawing.Size(560, 480)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false
    $dlg.MinimizeBox = $false

    $lblDlgTitle = New-Object System.Windows.Forms.Label
    $lblDlgTitle.Text = "Dispositivos Detectados y Guardados"
    $lblDlgTitle.Font = $script:Fonts.TitleMid
    $lblDlgTitle.ForeColor = $script:Theme.TextTitle
    $lblDlgTitle.Location = New-Object System.Drawing.Point(20, 16)
    $lblDlgTitle.Size = New-Object System.Drawing.Size(400, 24)
    $dlg.Controls.Add($lblDlgTitle)

    $lstDevices = New-Object System.Windows.Forms.ListBox
    $lstDevices.Font = $script:Fonts.Body
    $lstDevices.Location = New-Object System.Drawing.Point(20, 50)
    $lstDevices.Size = New-Object System.Drawing.Size(500, 220)
    $dlg.Controls.Add($lstDevices)

    # Cargar dispositivos
    $online = @()
    try { $online = @(Get-AdbDevices) } catch { }
    $saved = @()
    try { $saved = @(Get-SavedAdbDevices) } catch { }

    $itemsList = @()
    foreach ($d in $online) {
        $savedMatch = @($saved | Where-Object { $_.UsbSerial -ceq $d.Serial -or $_.LastEndpoint -ceq $d.Serial })[0]
        $name = if ($savedMatch) { $savedMatch.FriendlyName } else { $d.Serial }
        $lstDevices.Items.Add("📱 $name | Estado: $($d.Status) | Serial: $($d.Serial)") | Out-Null
        $itemsList += [pscustomobject]@{ Device = $d; IsOnline = $true }
    }
    foreach ($s in $saved) {
        $isAlreadyListed = @($online | Where-Object { $_.Serial -ceq $s.LastEndpoint -or $_.Serial -ceq $s.UsbSerial }).Count -gt 0
        if (-not $isAlreadyListed) {
            $lstDevices.Items.Add("💾 $($s.FriendlyName) (Guardado offline) | Ultimo: $($s.LastEndpoint)") | Out-Null
            $itemsList += [pscustomobject]@{ Device = $s; IsOnline = $false }
        }
    }
    if ($lstDevices.Items.Count -gt 0) { $lstDevices.SelectedIndex = 0 }

    # Conectar por IP manual
    $lblIp = New-Object System.Windows.Forms.Label
    $lblIp.Text = "Conectar por IP/Puerto (ej. 192.168.1.50:5555):"
    $lblIp.Font = $script:Fonts.Small
    $lblIp.ForeColor = $script:Theme.TextBody
    $lblIp.Location = New-Object System.Drawing.Point(20, 280)
    $lblIp.Size = New-Object System.Drawing.Size(320, 20)
    $dlg.Controls.Add($lblIp)

    $txtEndpointInput = New-Object System.Windows.Forms.TextBox
    $txtEndpointInput.Font = $script:Fonts.Body
    $txtEndpointInput.Location = New-Object System.Drawing.Point(20, 302)
    $txtEndpointInput.Size = New-Object System.Drawing.Size(360, 26)
    $dlg.Controls.Add($txtEndpointInput)

    $btnConnectIp = New-Object System.Windows.Forms.Button
    $btnConnectIp.Text = "Conectar IP"
    $btnConnectIp.Font = $script:Fonts.Small
    $btnConnectIp.BackColor = $script:Theme.Secondary
    $btnConnectIp.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnConnectIp.Location = New-Object System.Drawing.Point(390, 301)
    $btnConnectIp.Size = New-Object System.Drawing.Size(130, 28)
    $btnConnectIp.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnConnectIp.Add_Click({
        $ep = $txtEndpointInput.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($ep)) { return }
        $dlg.Close()
        Write-LogEntry -Level 'INFO' -Message "Conectando a endpoint $ep..."
        Invoke-GuiAsyncTask -Title "Conectar a $ep" -WorkerScript {
            param($targetEp)
            Connect-AdbEndpoint -Endpoint $targetEp
        } -Parameters @{ targetEp = $ep } -OnCompleted {
            param($res)
            Refresh-DeviceStatus
        }
    })
    $dlg.Controls.Add($btnConnectIp)

    # Boton Conectar Nuevo (abre el Asistente)
    $btnNewDevFromMgr = New-Object System.Windows.Forms.Button
    $btnNewDevFromMgr.Text = "✨ Conectar Nuevo..."
    $btnNewDevFromMgr.Font = $script:Fonts.BodyBold
    $btnNewDevFromMgr.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
    $btnNewDevFromMgr.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    $btnNewDevFromMgr.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnNewDevFromMgr.FlatAppearance.BorderSize = 0
    $btnNewDevFromMgr.Location = New-Object System.Drawing.Point(20, 370)
    $btnNewDevFromMgr.Size = New-Object System.Drawing.Size(180, 38)
    $btnNewDevFromMgr.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnNewDevFromMgr.Add_Click({
        $dlg.Close()
        Show-NewDeviceWizard
    })
    $dlg.Controls.Add($btnNewDevFromMgr)

    # Boton Seleccionar
    $btnSelect = New-Object System.Windows.Forms.Button
    $btnSelect.Text = "Seleccionar como Activo"
    $btnSelect.Font = $script:Fonts.BodyBold
    $btnSelect.BackColor = $script:Theme.Primary
    $btnSelect.ForeColor = $script:Theme.PrimaryFg
    $btnSelect.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnSelect.FlatAppearance.BorderSize = 0
    $btnSelect.Location = New-Object System.Drawing.Point(215, 370)
    $btnSelect.Size = New-Object System.Drawing.Size(200, 38)
    $btnSelect.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnSelect.Add_Click({
        if ($lstDevices.SelectedIndex -ge 0) {
            $selectedItem = $itemsList[$lstDevices.SelectedIndex]
            try {
                Set-CurrentAdbDevice -Device $selectedItem.Device -Persist | Out-Null
                Write-LogEntry -Level 'SUCCESS' -Message "Dispositivo seleccionado como activo."
            } catch {
                Write-LogEntry -Level 'ERROR' -Message "No se pudo seleccionar: $($_.Exception.Message)"
            }
            $dlg.Close()
            Refresh-DeviceStatus
        }
    })
    $dlg.Controls.Add($btnSelect)

    $null = $dlg.ShowDialog($form)
})

# 2. TRANSFERIR ARCHIVOS
$btnActionTransfer.Add_Click({
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Transferir Archivos Android"
    $dlg.Size = New-Object System.Drawing.Size(520, 380)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Opciones de Transferencia Rapida"
    $lbl.Font = $script:Fonts.TitleMid
    $lbl.Location = New-Object System.Drawing.Point(24, 20)
    $lbl.Size = New-Object System.Drawing.Size(460, 28)
    $dlg.Controls.Add($lbl)

    $btnPush = New-Object System.Windows.Forms.Button
    $btnPush.Text = "📤 Subir archivo a /sdcard/Download/"
    $btnPush.Font = $script:Fonts.BodyBold
    $btnPush.Size = New-Object System.Drawing.Size(450, 48)
    $btnPush.Location = New-Object System.Drawing.Point(24, 60)
    $btnPush.BackColor = $script:Theme.BtnActionBg
    $btnPush.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnPush.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnPush.Add_Click({
        $dlg.Close()
        $ofd = New-Object System.Windows.Forms.OpenFileDialog
        $ofd.Title = "Selecciona el archivo para enviar a Android"
        if ($ofd.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $filePath = $ofd.FileName
            Write-LogEntry -Level 'INFO' -Message "Subiendo '$filePath' a /sdcard/Download/..."
            Invoke-GuiAsyncTask -Title "Subir archivo" -WorkerScript {
                param($local)
                $cur = Get-CurrentAdbDevice
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('push', $local, '/sdcard/Download/')
            } -Parameters @{ local = $filePath } -OnCompleted {
                Write-LogEntry -Level 'SUCCESS' -Message "Archivo subido exitosamente a /sdcard/Download/"
            }
        }
    })
    $dlg.Controls.Add($btnPush)

    $btnPullPhotos = New-Object System.Windows.Forms.Button
    $btnPullPhotos.Text = "📥 Descargar fotos recientes (ultimos 7 dias)"
    $btnPullPhotos.Font = $script:Fonts.BodyBold
    $btnPullPhotos.Size = New-Object System.Drawing.Size(450, 48)
    $btnPullPhotos.Location = New-Object System.Drawing.Point(24, 120)
    $btnPullPhotos.BackColor = $script:Theme.BtnActionBg
    $btnPullPhotos.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnPullPhotos.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnPullPhotos.Add_Click({
        $dlg.Close()
        $targetDir = Join-Path (Join-Path $env:USERPROFILE 'Downloads') 'AndroidPhotos'
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        Write-LogEntry -Level 'INFO' -Message "Buscando fotos recientes de la camara..."
        Invoke-GuiAsyncTask -Title "Descargar fotos recientes" -WorkerScript {
            param($dest)
            $cur = Get-CurrentAdbDevice
            $files = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','find','/sdcard/DCIM/Camera','-type','f','-mtime','-7','-print') | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
            $count = 0
            foreach ($f in $files) {
                $trimmed = ([string]$f).Trim()
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', $trimmed, $dest) | Out-Null
                $count++
            }
            return $count
        } -Parameters @{ dest = $targetDir } -OnCompleted {
            param($res)
            $c = if ($res.Output) { $res.Output[0] } else { 0 }
            Write-LogEntry -Level 'SUCCESS' -Message "Se descargaron $c fotos recientes en $targetDir"
            Start-Process -FilePath 'explorer.exe' -ArgumentList $targetDir
        }
    })
    $dlg.Controls.Add($btnPullPhotos)

    $btnPullDownloads = New-Object System.Windows.Forms.Button
    $btnPullDownloads.Text = "📥 Descargar carpeta /sdcard/Download/ a la PC"
    $btnPullDownloads.Font = $script:Fonts.BodyBold
    $btnPullDownloads.Size = New-Object System.Drawing.Size(450, 48)
    $btnPullDownloads.Location = New-Object System.Drawing.Point(24, 180)
    $btnPullDownloads.BackColor = $script:Theme.BtnActionBg
    $btnPullDownloads.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnPullDownloads.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnPullDownloads.Add_Click({
        $dlg.Close()
        $destDir = Join-Path (Join-Path $env:USERPROFILE 'Downloads') 'AndroidDownloads'
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        Write-LogEntry -Level 'INFO' -Message "Descargando carpeta /sdcard/Download/ a '$destDir'..."
        Invoke-GuiAsyncTask -Title "Descargar Downloads" -WorkerScript {
            param($dest)
            $cur = Get-CurrentAdbDevice
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', '/sdcard/Download/', $dest)
        } -Parameters @{ dest = $destDir } -OnCompleted {
            Write-LogEntry -Level 'SUCCESS' -Message "Carpeta descargada con exito."
            Start-Process -FilePath 'explorer.exe' -ArgumentList $destDir
        }
    })
    $dlg.Controls.Add($btnPullDownloads)

    $btnScriptInteractive = New-Object System.Windows.Forms.Button
    $btnScriptInteractive.Text = "💻 Abrir Asistente Completo en Consola"
    $btnScriptInteractive.Font = $script:Fonts.Small
    $btnScriptInteractive.Size = New-Object System.Drawing.Size(450, 36)
    $btnScriptInteractive.Location = New-Object System.Drawing.Point(24, 250)
    $btnScriptInteractive.BackColor = $script:Theme.Secondary
    $btnScriptInteractive.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnScriptInteractive.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnScriptInteractive.Add_Click({
        $dlg.Close()
        $tScript = Join-Path $script:RootPath 'android_transfer.ps1'
        Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoProfile -ExecutionPolicy RemoteSigned -File `"$tScript`""
    })
    $dlg.Controls.Add($btnScriptInteractive)

    $null = $dlg.ShowDialog($form)
})

# 3. EXPLORADOR DE ARCHIVOS
$btnActionExplorer.Add_Click({
    Write-LogEntry -Level 'INFO' -Message "Listando contenido de /sdcard/ en el dispositivo..."
    Invoke-GuiAsyncTask -Title "Explorar /sdcard/" -WorkerScript {
        $cur = Get-CurrentAdbDevice
        Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','ls','-lah','/sdcard/')
    } -OnCompleted {
        param($res)
        if ($res -and $res.Output) {
            Write-LogEntry -Level 'SUCCESS' -Message "--- Contenido de /sdcard/ ---"
            foreach ($line in $res.Output) {
                Write-LogEntry -Level 'INFO' -Message ([string]$line)
            }
        }
    }
})

# 4. BACKUP AVANZADO
$btnActionBackup.Add_Click({
    $confirm = [System.Windows.Forms.MessageBox]::Show("¿Deseas iniciar el respaldo automatico de carpetas principales (DCIM, Documentos, Descargas)?`n`nSe guardara en la carpeta Downloads de tu usuario sin congelar el sistema.", "Confirmar Backup", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
        $backupDir = Join-Path (Join-Path $env:USERPROFILE 'Downloads') ("AndroidBackup_" + [DateTime]::Now.ToString('yyyyMMdd_HHmmss'))
        Write-LogEntry -Level 'INFO' -Message "Iniciando respaldo en '$backupDir'..."
        Invoke-GuiAsyncTask -Title "Backup Avanzado" -WorkerScript {
            param($dest)
            $cur = Get-CurrentAdbDevice
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
            $subFolders = @('DCIM', 'Documents', 'Download', 'Pictures')
            foreach ($folder in $subFolders) {
                $localTarget = Join-Path $dest $folder
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', "/sdcard/$folder/", $localTarget) | Out-Null
            }
            return $dest
        } -Parameters @{ dest = $backupDir } -OnCompleted {
            param($res)
            Write-LogEntry -Level 'SUCCESS' -Message "Backup completado exitosamente en $backupDir"
            Start-Process -FilePath 'explorer.exe' -ArgumentList $backupDir
        }
    }
})

# 5. GESTION DE APLICACIONES
$btnActionApps.Add_Click({
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Gestion de Aplicaciones"
    $dlg.Size = New-Object System.Drawing.Size(560, 480)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Aplicaciones de Terceros Instaladas"
    $lbl.Font = $script:Fonts.TitleMid
    $lbl.Location = New-Object System.Drawing.Point(20, 16)
    $lbl.Size = New-Object System.Drawing.Size(350, 24)
    $dlg.Controls.Add($lbl)

    $lstApps = New-Object System.Windows.Forms.ListBox
    $lstApps.Font = $script:Fonts.Body
    $lstApps.Location = New-Object System.Drawing.Point(20, 48)
    $lstApps.Size = New-Object System.Drawing.Size(500, 260)
    $dlg.Controls.Add($lstApps)

    # Cargar paquetes
    $cur = $null
    try { $cur = Get-CurrentAdbDevice } catch { }
    if ($cur -and $cur.Status -eq 'device') {
        try {
            $pkgs = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','pm','list','packages','-3')) | ForEach-Object {
                ([string]$_) -replace '^package:', ''
            } | Sort-Object
            foreach ($p in $pkgs) {
                if (-not [string]::IsNullOrWhiteSpace($p)) { $lstApps.Items.Add($p) | Out-Null }
            }
        } catch { }
    }

    $btnInstallApk = New-Object System.Windows.Forms.Button
    $btnInstallApk.Text = "📦 Instalar APK..."
    $btnInstallApk.Font = $script:Fonts.BodyBold
    $btnInstallApk.BackColor = $script:Theme.Primary
    $btnInstallApk.ForeColor = $script:Theme.PrimaryFg
    $btnInstallApk.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnInstallApk.Location = New-Object System.Drawing.Point(20, 325)
    $btnInstallApk.Size = New-Object System.Drawing.Size(180, 38)
    $btnInstallApk.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnInstallApk.Add_Click({
        $dlg.Close()
        $ofd = New-Object System.Windows.Forms.OpenFileDialog
        $ofd.Filter = "Archivos APK (*.apk)|*.apk"
        $ofd.Title = "Selecciona el paquete APK para instalar"
        if ($ofd.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $apk = $ofd.FileName
            Write-LogEntry -Level 'INFO' -Message "Instalando '$apk'..."
            Invoke-GuiAsyncTask -Title "Instalar APK" -WorkerScript {
                param($apkFile)
                $curDev = Get-CurrentAdbDevice
                Invoke-AdbCommand -Serial $curDev.Serial -Arguments @('install','-r',$apkFile)
            } -Parameters @{ apkFile = $apk } -OnCompleted {
                Write-LogEntry -Level 'SUCCESS' -Message "Instalacion de APK finalizada."
            }
        }
    })
    $dlg.Controls.Add($btnInstallApk)

    $btnUninstall = New-Object System.Windows.Forms.Button
    $btnUninstall.Text = "🗑️ Desinstalar Seleccionado"
    $btnUninstall.Font = $script:Fonts.BodyBold
    $btnUninstall.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FEE2E2')
    $btnUninstall.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
    $btnUninstall.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnUninstall.Location = New-Object System.Drawing.Point(215, 325)
    $btnUninstall.Size = New-Object System.Drawing.Size(230, 38)
    $btnUninstall.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnUninstall.Add_Click({
        if ($lstApps.SelectedIndex -ge 0) {
            $pkgSelected = [string]$lstApps.SelectedItem
            $confirm = [System.Windows.Forms.MessageBox]::Show("¿Seguro que deseas desinstalar el paquete:`n$pkgSelected?", "Confirmar Desinstalacion", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning)
            if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
                $dlg.Close()
                Write-LogEntry -Level 'INFO' -Message "Desinstalando '$pkgSelected'..."
                Invoke-GuiAsyncTask -Title "Desinstalar paquete" -WorkerScript {
                    param($pkgName)
                    $curDev = Get-CurrentAdbDevice
                    Invoke-AdbCommand -Serial $curDev.Serial -Arguments @('shell','pm','uninstall',$pkgName)
                } -Parameters @{ pkgName = $pkgSelected } -OnCompleted {
                    Write-LogEntry -Level 'SUCCESS' -Message "Desinstalacion de $pkgSelected completada."
                }
            }
        }
    })
    $dlg.Controls.Add($btnUninstall)

    $null = $dlg.ShowDialog($form)
})

# 6. CAPTURA / GRABACION DE PANTALLA
$btnActionScreen.Add_Click({
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Herramientas de Pantalla"
    $dlg.Size = New-Object System.Drawing.Size(460, 240)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false

    $btnCap = New-Object System.Windows.Forms.Button
    $btnCap.Text = "📸 Tomar Captura de Pantalla (Screenshot)"
    $btnCap.Font = $script:Fonts.BodyBold
    $btnCap.Size = New-Object System.Drawing.Size(400, 48)
    $btnCap.Location = New-Object System.Drawing.Point(24, 25)
    $btnCap.BackColor = $script:Theme.BtnActionBg
    $btnCap.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnCap.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnCap.Add_Click({
        $dlg.Close()
        Write-LogEntry -Level 'INFO' -Message "Tomando captura de pantalla..."
        Invoke-GuiAsyncTask -Title "Captura de pantalla" -WorkerScript {
            Capture-AdbScreenshot -OpenAfterCapture
        } -OnCompleted {
            param($res)
            if ($res -and $res.Output) {
                $file = $res.Output[0]
                Write-LogEntry -Level 'SUCCESS' -Message "Captura guardada en $($file.FullName)"
            }
        }
    })
    $dlg.Controls.Add($btnCap)

    $btnScrcpy = New-Object System.Windows.Forms.Button
    $btnScrcpy.Text = "🎬 Transmitir Pantalla con scrcpy (Espejo interactivo)"
    $btnScrcpy.Font = $script:Fonts.BodyBold
    $btnScrcpy.Size = New-Object System.Drawing.Size(400, 48)
    $btnScrcpy.Location = New-Object System.Drawing.Point(24, 85)
    $btnScrcpy.BackColor = $script:Theme.BtnActionBg
    $btnScrcpy.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnScrcpy.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnScrcpy.Add_Click({
        $dlg.Close()
        Write-LogEntry -Level 'INFO' -Message "Comprobando e iniciando scrcpy..."
        try {
            if (Test-ScrcpyAvailable) {
                Start-AdbScrcpy | Out-Null
                Write-LogEntry -Level 'SUCCESS' -Message "scrcpy iniciado."
            }
        } catch {
            Write-LogEntry -Level 'WARN' -Message "scrcpy no se encuentra instalado en el equipo: $($_.Exception.Message)"
        }
    })
    $dlg.Controls.Add($btnScrcpy)

    $null = $dlg.ShowDialog($form)
})

# 7. LIMPIEZA DEL DISPOSITIVO
$btnActionClean.Add_Click({
    $confirm = [System.Windows.Forms.MessageBox]::Show("¿Deseas ejecutar la limpieza de cache de aplicaciones en el dispositivo?", "Confirmar Limpieza", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
        Write-LogEntry -Level 'INFO' -Message "Ejecutando limpieza de cache en el dispositivo (pm trim-caches)..."
        Invoke-GuiAsyncTask -Title "Limpieza de cache" -WorkerScript {
            $cur = Get-CurrentAdbDevice
            $before = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','df','-h','/sdcard'))
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','pm','trim-caches','1000M') | Out-Null
            $after = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','df','-h','/sdcard'))
            return [pscustomobject]@{ Before = $before; After = $after }
        } -OnCompleted {
            param($res)
            Write-LogEntry -Level 'SUCCESS' -Message "Limpieza de cache finalizada con exito."
        }
    }
})

# 8. DIAGNOSTICO COMPLETO
$btnActionDiag.Add_Click({
    Write-LogEntry -Level 'INFO' -Message "Iniciando analisis de diagnostico del entorno ADB..."
    Invoke-GuiAsyncTask -Title "Diagnostico Completo" -WorkerScript {
        Invoke-AdbDiagnostics -NoPause
    } -OnCompleted {
        param($res)
        if ($res -and $res.Output) {
            $diag = $res.Output[0]
            Write-LogEntry -Level 'SUCCESS' -Message "Diagnostico completado:"
            Write-LogEntry -Level 'INFO' -Message "Ruta ADB: $($diag.AdbPath)"
            Write-LogEntry -Level 'INFO' -Message "Dispositivos detectados: $(@($diag.Devices).Count)"
            Write-LogEntry -Level 'INFO' -Message "Dispositivos guardados: $($diag.SavedCount)"
            if ($diag.Recommendations -and @($diag.Recommendations).Count -gt 0) {
                Write-LogEntry -Level 'WARN' -Message "Recomendaciones encontradas:"
                foreach ($rec in $diag.Recommendations) {
                    Write-LogEntry -Level 'WARN' -Message " - $rec"
                }
            } else {
                Write-LogEntry -Level 'SUCCESS' -Message "No se detectaron problemas en el entorno ADB."
            }
        }
    }
})

# =============================================================================
# INICIALIZACION Y EVENTO SHOWN
# =============================================================================
$form.Add_Shown({
    Load-FavoritesUi
    Write-LogEntry -Level 'INFO' -Message 'Bienvenido a Android ADB Tools. Inicializando interfaz...'
    Refresh-DeviceStatus -Silent
    Invoke-UsbAutoDetection

    $script:UsbAutoScanTimer = New-Object System.Windows.Forms.Timer
    $script:UsbAutoScanTimer.Interval = 3000
    $script:UsbAutoScanTimer.Add_Tick({ Invoke-UsbAutoDetection })
    $script:UsbAutoScanTimer.Start()
})

$form.Add_FormClosed({
    if ($null -ne $script:UsbAutoScanTimer) {
        $script:UsbAutoScanTimer.Stop()
        $script:UsbAutoScanTimer.Dispose()
        $script:UsbAutoScanTimer = $null
    }
})

# Lanzar la ventana
[void]$form.ShowDialog()
