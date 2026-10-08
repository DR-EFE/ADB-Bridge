<#
.SYNOPSIS
    Android ADB Tools - Interfaz Grafica Moderna (WinForms).
.DESCRIPTION
    Panel de control moderno, rapido y fluido para Android ADB Tools en Windows PowerShell 5.1.
    Incluye:
    - Espacio de trabajo central con pestanas interactivas:
      * 📁 Explorador de Archivos Android: Navegacion visual por carpetas, subir, descargar y borrar.
      * ⇄ Transferencias & Fotos: Descarga de fotos recientes, DCIM, descargas, documentos y backups con progreso en vivo.
      * 📱 Gestion de Dispositivos: Tarjetas de dispositivos, conversion USB a WiFi, ping y diagnostico.
      * 📜 Registro & Salida: Visor de log formateado para operaciones ADB.
    - Modulo independiente y ventana completa para MODO AVANZADO (Consola ADB + Biblioteca categorizada).
    - Escaneo continuo inteligente de USB con auto-conexion y auto-guardado en WiFi.
    - Notificaciones visuales interactivas en tiempo real.
    - Ejecucion completamente asincrona (sin bloqueos de interfaz).
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
    Write-AdbLog -Level INFO -Source 'AndroidTools.GUI.ps1' -Message 'Iniciando interfaz grafica WinForms moderna con Workspace Interactivo.'
}

# -----------------------------------------------------------------------------
# PALETA DE COLORES Y ESTILOS MODERNOS (Segoe UI + Tailwind/Modern Tokens)
# -----------------------------------------------------------------------------
$script:Theme = @{
    BgWindow        = [System.Drawing.ColorTranslator]::FromHtml('#F8FAFC')
    BgCard          = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    BgHeader        = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    BorderCard      = [System.Drawing.ColorTranslator]::FromHtml('#E2E8F0')
    BorderLight     = [System.Drawing.ColorTranslator]::FromHtml('#F1F5F9')
    
    # Acentos Principales
    Primary         = [System.Drawing.ColorTranslator]::FromHtml('#2563EB') # Azul moderno
    PrimaryHover    = [System.Drawing.ColorTranslator]::FromHtml('#1D4ED8')
    PrimaryActive   = [System.Drawing.ColorTranslator]::FromHtml('#1E40AF')
    PrimaryFg       = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    
    # Secundarios y Botones
    Secondary       = [System.Drawing.ColorTranslator]::FromHtml('#F1F5F9')
    SecondaryHover  = [System.Drawing.ColorTranslator]::FromHtml('#E2E8F0')
    SecondaryFg     = [System.Drawing.ColorTranslator]::FromHtml('#334155')
    
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
    
    # Badges y Notificaciones
    BadgeSuccessBg  = [System.Drawing.ColorTranslator]::FromHtml('#DCFCE7')
    BadgeSuccessFg  = [System.Drawing.ColorTranslator]::FromHtml('#15803D')
    BadgeWarningBg  = [System.Drawing.ColorTranslator]::FromHtml('#FEF3C7')
    BadgeWarningFg  = [System.Drawing.ColorTranslator]::FromHtml('#B45309')
    BadgeErrorBg    = [System.Drawing.ColorTranslator]::FromHtml('#FEE2E2')
    BadgeErrorFg    = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
    BadgeInfoBg     = [System.Drawing.ColorTranslator]::FromHtml('#DBEAFE')
    BadgeInfoFg     = [System.Drawing.ColorTranslator]::FromHtml('#1D4ED8')
    BadgeMutedBg    = [System.Drawing.ColorTranslator]::FromHtml('#F1F5F9')
    BadgeMutedFg    = [System.Drawing.ColorTranslator]::FromHtml('#64748B')
}

$script:Fonts = @{
    TitleLarge = New-Object System.Drawing.Font('Segoe UI', 13, [System.Drawing.FontStyle]::Bold)
    TitleMid   = New-Object System.Drawing.Font('Segoe UI', 10.5, [System.Drawing.FontStyle]::Bold)
    TitleSmall = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Bold)
    Body       = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Regular)
    BodyBold   = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Bold)
    Small      = New-Object System.Drawing.Font('Segoe UI', 8.5, [System.Drawing.FontStyle]::Regular)
    SmallBold  = New-Object System.Drawing.Font('Segoe UI', 8.5, [System.Drawing.FontStyle]::Bold)
    Badge      = New-Object System.Drawing.Font('Segoe UI', 8.5, [System.Drawing.FontStyle]::Bold)
    Code       = New-Object System.Drawing.Font('Consolas', 9.5, [System.Drawing.FontStyle]::Regular)
    CodeBold   = New-Object System.Drawing.Font('Consolas', 10, [System.Drawing.FontStyle]::Bold)
    CodeLarge  = New-Object System.Drawing.Font('Consolas', 10.5, [System.Drawing.FontStyle]::Regular)
}

# -----------------------------------------------------------------------------
# UTILIDADES DE FORMATO
# -----------------------------------------------------------------------------
function Format-GuiFileSize {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return "{0:N2} GB" -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return "{0:N2} MB" -f ($Bytes / 1MB) }
    if ($Bytes -ge 1KB) { return "{0:N1} KB" -f ($Bytes / 1KB) }
    return "$Bytes B"
}

# -----------------------------------------------------------------------------
# PERSISTENCIA ROBUSTA DE DISPOSITIVOS (devices.json)
# -----------------------------------------------------------------------------
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

    $store = Read-AdbDeviceStore
    $existing = @($store.Devices | Where-Object {
        ($UsbSerial -and $_.UsbSerial -and $_.UsbSerial -ceq $UsbSerial) -or
        ($LastEndpoint -and $_.LastEndpoint -and $_.LastEndpoint -ieq $LastEndpoint)
    })[0]

    $timestamp = [DateTime]::UtcNow.ToString('o')
    if ($null -ne $existing) {
        return (Update-SavedAdbDevice -Id $existing.Id -FriendlyName $FriendlyName -UsbSerial $UsbSerial -LastEndpoint $LastEndpoint -Mode $Mode -IsPaired $IsPaired -LastConnectedUtc $timestamp)
    }
    return (Add-SavedAdbDevice -FriendlyName $FriendlyName -Mode $Mode -UsbSerial $UsbSerial -LastEndpoint $LastEndpoint -IsPaired $IsPaired -LastConnectedUtc $timestamp)
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

    $defaults = @(
        [pscustomobject]@{ Name = '📱 Modelo y fabricante'; Command = 'shell getprop ro.product.model' },
        [pscustomobject]@{ Name = '🤖 Version de Android'; Command = 'shell getprop ro.build.version.release' },
        [pscustomobject]@{ Name = '🔋 Estado de bateria y salud'; Command = 'shell dumpsys battery' },
        [pscustomobject]@{ Name = '💾 Espacio en almacenamiento'; Command = 'shell df -h /sdcard' },
        [pscustomobject]@{ Name = '🌐 Direccion IP WiFi (wlan0)'; Command = 'shell ip -4 addr show wlan0' },
        [pscustomobject]@{ Name = '📦 Apps instaladas por usuario'; Command = 'shell pm list packages -3' },
        [pscustomobject]@{ Name = '🧠 Uso detallado de memoria RAM'; Command = 'shell dumpsys meminfo' },
        [pscustomobject]@{ Name = '📊 Consumo de CPU por proceso'; Command = 'shell dumpsys cpuinfo' },
        [pscustomobject]@{ Name = '📋 Listar dispositivos ADB'; Command = 'devices -l' },
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
# BIBLIOTECA COMPLETA DE COMANDOS AVANZADOS
# -----------------------------------------------------------------------------
$script:AdvancedCommandExamples = @(
    # Diagnóstico y Rendimiento
    [pscustomobject]@{ Category = 'Diagnostico & Rendimiento'; Name = '📊 Uso de CPU (dumpsys)'; Command = 'shell dumpsys cpuinfo'; Description = 'Muestra consumo de CPU por proceso' },
    [pscustomobject]@{ Category = 'Diagnostico & Rendimiento'; Name = '🧠 Detalle de memoria RAM'; Command = 'shell dumpsys meminfo'; Description = 'Uso de memoria RAM del sistema y procesos' },
    [pscustomobject]@{ Category = 'Diagnostico & Rendimiento'; Name = '🔋 Diagnostico de bateria y salud'; Command = 'shell dumpsys battery'; Description = 'Nivel, voltaje, temperatura y salud' },
    [pscustomobject]@{ Category = 'Diagnostico & Rendimiento'; Name = '💾 Espacio en almacenamiento'; Command = 'shell df -h /sdcard'; Description = 'Espacio libre y usado en /sdcard' },
    [pscustomobject]@{ Category = 'Diagnostico & Rendimiento'; Name = '📈 Top 10 procesos activos'; Command = 'shell top -n 1 -m 10'; Description = 'Procesos con mayor consumo de recursos' },

    # Batería y Sensores
    [pscustomobject]@{ Category = 'Bateria & Sensores'; Name = '🔋 Simular bateria al 100%'; Command = 'shell dumpsys battery set level 100'; Description = 'Fuerza el nivel de bateria al 100%' },
    [pscustomobject]@{ Category = 'Bateria & Sensores'; Name = '🔋 Simular bateria baja (15%)'; Command = 'shell dumpsys battery set level 15'; Description = 'Fuerza aviso de bateria baja' },
    [pscustomobject]@{ Category = 'Bateria & Sensores'; Name = '🔋 Simular cargador de pared (AC)'; Command = 'shell dumpsys battery set ac 1'; Description = 'Simula conexion a cargador AC' },
    [pscustomobject]@{ Category = 'Bateria & Sensores'; Name = '🔋 Simular cargador USB'; Command = 'shell dumpsys battery set usb 1'; Description = 'Simula conexion a puerto USB' },
    [pscustomobject]@{ Category = 'Bateria & Sensores'; Name = '🔋 Restaurar sensor de bateria'; Command = 'shell dumpsys battery reset'; Description = 'Restaura medicion real del hardware' },

    # Sistema, Pantalla & Hardware
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '📱 Modelo exacto de telefono'; Command = 'shell getprop ro.product.model'; Description = 'Nombre del modelo comercial' },
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '🤖 Version de Android'; Command = 'shell getprop ro.build.version.release'; Description = 'Ejemplo: 12, 13, 14, 15' },
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '🔢 Nivel de API SDK'; Command = 'shell getprop ro.build.version.sdk'; Description = 'Ejemplo: 31, 33, 34, 35' },
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '🖥️ Resolucion de pantalla'; Command = 'shell wm size'; Description = 'Resolucion fisica en pixeles' },
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '📐 Densidad de pantalla (DPI)'; Command = 'shell wm density'; Description = 'DPI configurado actual' },
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '🏭 Fabricante del equipo'; Command = 'shell getprop ro.product.manufacturer'; Description = 'Marca del fabricante' },
    [pscustomobject]@{ Category = 'Sistema & Pantalla'; Name = '⚙️ Arquitectura CPU (ABI)'; Command = 'shell getprop ro.product.cpu.abi'; Description = 'Ejemplo: arm64-v8a, armeabi-v7a' },

    # Apps y Paquetes
    [pscustomobject]@{ Category = 'Apps & Paquetes'; Name = '📦 Apps instaladas por usuario'; Command = 'shell pm list packages -3'; Description = 'Filtra solo apps no de sistema' },
    [pscustomobject]@{ Category = 'Apps & Paquetes'; Name = '📦 Apps de sistema'; Command = 'shell pm list packages -s'; Description = 'Apps preinstaladas en ROM' },
    [pscustomobject]@{ Category = 'Apps & Paquetes'; Name = '📦 Apps deshabilitadas'; Command = 'shell pm list packages -d'; Description = 'Paquetes inactivos o congelados' },
    [pscustomobject]@{ Category = 'Apps & Paquetes'; Name = '📦 Ver actividad en primer plano'; Command = 'shell dumpsys window | grep -E "mCurrentFocus"'; Description = 'Ventana y app activa en pantalla' },

    # Red y Conectividad
    [pscustomobject]@{ Category = 'Red & Conectividad'; Name = '🌐 Direccion IP WiFi (wlan0)'; Command = 'shell ip -4 addr show wlan0'; Description = 'IP asignada en red local WiFi' },
    [pscustomobject]@{ Category = 'Red & Conectividad'; Name = '🌐 Todas las interfaces de red'; Command = 'shell ip addr'; Description = 'wlan0, rmnet, loopback, etc.' },
    [pscustomobject]@{ Category = 'Red & Conectividad'; Name = '🌐 Prueba de ping a Google DNS'; Command = 'shell ping -c 3 8.8.8.8'; Description = 'Verifica salida a internet' },
    [pscustomobject]@{ Category = 'Red & Conectividad'; Name = '🌐 Conexiones y puertos activos'; Command = 'shell netstat -tlpn'; Description = 'Sockets TCP/UDP abiertos' },

    # Control, Teclas y Gestos
    [pscustomobject]@{ Category = 'Control & Teclas'; Name = '🎮 Presionar boton Power'; Command = 'shell input keyevent 26'; Description = 'Enciende o apaga la pantalla' },
    [pscustomobject]@{ Category = 'Control & Teclas'; Name = '🎮 Presionar boton Home'; Command = 'shell input keyevent 3'; Description = 'Va a la pantalla de inicio' },
    [pscustomobject]@{ Category = 'Control & Teclas'; Name = '🎮 Presionar boton Atras (Back)'; Command = 'shell input keyevent 4'; Description = 'Boton atras del sistema' },
    [pscustomobject]@{ Category = 'Control & Teclas'; Name = '🎮 Despertar pantalla (Wakeup)'; Command = 'shell input keyevent 224'; Description = 'Enciende pantalla sin bloquear' },
    [pscustomobject]@{ Category = 'Control & Teclas'; Name = '🎮 Abrir panel de Notificaciones'; Command = 'shell cmd statusbar expand-notifications'; Description = 'Despliega la barra superior' },
    [pscustomobject]@{ Category = 'Control & Teclas'; Name = '🎮 Deslizar hacia arriba (Swipe)'; Command = 'shell input swipe 500 1500 500 500 200'; Description = 'Gesto de scroll vertical' },

    # Logcat y Depuración
    [pscustomobject]@{ Category = 'Logcat & Logs'; Name = '📜 Ver solo errores (Error Log)'; Command = 'logcat -d -v time *:E'; Description = 'Filtra solo excepciones y fallos' },
    [pscustomobject]@{ Category = 'Logcat & Logs'; Name = '📜 Limpiar buffer de logcat'; Command = 'logcat -c'; Description = 'Borra el historial de logs en RAM' },
    [pscustomobject]@{ Category = 'Logcat & Logs'; Name = '📜 Log del Kernel Linux (dmesg)'; Command = 'shell dmesg'; Description = 'Eventos de drivers y kernel' },

    # Reinicio y Modos Especiales
    [pscustomobject]@{ Category = 'Reinicio & Modos'; Name = '🔄 Reiniciar normalmente'; Command = 'reboot'; Description = 'Reinicio estandar del sistema' },
    [pscustomobject]@{ Category = 'Reinicio & Modos'; Name = '🔄 Reiniciar a modo Recovery'; Command = 'reboot recovery'; Description = 'Menu de recuperacion de fabrica' },
    [pscustomobject]@{ Category = 'Reinicio & Modos'; Name = '🔄 Reiniciar a modo Bootloader'; Command = 'reboot bootloader'; Description = 'Modo Fastboot / Bootloader' }
)

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

                if ($OnCompleted) {
                    & $OnCompleted $resObj
                }
            } catch {
                Write-LogEntry -Level 'ERROR' -Message "Excepcion en callback de ${Title}: $($_.Exception.Message)"
            } finally {
                $ps.Dispose()
            }
        }
    })
    $timer.Start()
}

function Set-UiBusyState {
    param([bool]$IsBusy, [string]$Operation)
    if ($null -eq $form -or $form.IsDisposed) { return }

    if ($IsBusy) {
        $pbProgress.Visible = $true
        $pbProgress.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
        $lblLastOp.Text = "Ejecutando: $Operation..."
        $lblLastOp.ForeColor = $script:Theme.Primary
    } else {
        $pbProgress.Visible = $false
        $lblLastOp.Text = "Listo"
        $lblLastOp.ForeColor = $script:Theme.TextMuted
    }
}

function Write-LogEntry {
    param(
        [Parameter(Mandatory=$true)][string]$Message,
        [ValidateSet('INFO','SUCCESS','WARN','ERROR')][string]$Level = 'INFO'
    )
    if ($null -eq $txtLog -or $txtLog.IsDisposed) { return }

    $time = Get-Date -Format 'HH:mm:ss'
    $color = switch ($Level) {
        'SUCCESS' { [System.Drawing.ColorTranslator]::FromHtml('#4ADE80') }
        'WARN'    { [System.Drawing.ColorTranslator]::FromHtml('#FBBF24') }
        'ERROR'   { [System.Drawing.ColorTranslator]::FromHtml('#F87171') }
        default   { [System.Drawing.ColorTranslator]::FromHtml('#94A3B8') }
    }

    $txtLog.SelectionStart = $txtLog.TextLength
    $txtLog.SelectionLength = 0
    $txtLog.SelectionColor = [System.Drawing.ColorTranslator]::FromHtml('#64748B')
    $txtLog.AppendText("[$time] ")

    $txtLog.SelectionColor = $color
    $txtLog.AppendText("[$Level] ")

    $txtLog.SelectionColor = $script:Theme.LogFg
    $txtLog.AppendText("$Message`r`n")
    $txtLog.ScrollToCaret()

    # Si hay una consola avanzada abierta, actualizar tambien
    if ($null -ne $script:txtAdvConsoleOutput -and -not $script:txtAdvConsoleOutput.IsDisposed) {
        $script:txtAdvConsoleOutput.SelectionStart = $script:txtAdvConsoleOutput.TextLength
        $script:txtAdvConsoleOutput.SelectionLength = 0
        $script:txtAdvConsoleOutput.SelectionColor = $color
        $script:txtAdvConsoleOutput.AppendText("[$time] [$Level] $Message`r`n")
        $script:txtAdvConsoleOutput.ScrollToCaret()
    }
}

# -----------------------------------------------------------------------------
# SISTEMA DE NOTIFICACIONES VISUALES INTERACTIVAS
# -----------------------------------------------------------------------------
$script:NotifTimer = $null

function Show-NotificationBanner {
    param(
        [Parameter(Mandatory=$true)][ValidateSet('SUCCESS','WARN','ERROR','INFO')][string]$Type,
        [Parameter(Mandatory=$true)][string]$Title,
        [Parameter(Mandatory=$true)][string]$Message,
        [string]$ActionText = $null,
        [scriptblock]$ActionScript = $null,
        [int]$AutoCloseSeconds = 8
    )

    if ($null -eq $pnlNotification -or $pnlNotification.IsDisposed) { return }

    switch ($Type) {
        'SUCCESS' {
            $pnlNotification.BackColor = $script:Theme.BadgeSuccessBg
            $lblNotifTitle.ForeColor = $script:Theme.BadgeSuccessFg
            $lblNotifMessage.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#14532D')
            $lblNotifIcon.Text = "✅"
        }
        'WARN' {
            $pnlNotification.BackColor = $script:Theme.BadgeWarningBg
            $lblNotifTitle.ForeColor = $script:Theme.BadgeWarningFg
            $lblNotifMessage.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#78350F')
            $lblNotifIcon.Text = "⚠️"
        }
        'ERROR' {
            $pnlNotification.BackColor = $script:Theme.BadgeErrorBg
            $lblNotifTitle.ForeColor = $script:Theme.BadgeErrorFg
            $lblNotifMessage.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#7F1D1D')
            $lblNotifIcon.Text = "❌"
        }
        default {
            $pnlNotification.BackColor = $script:Theme.BadgeInfoBg
            $lblNotifTitle.ForeColor = $script:Theme.BadgeInfoFg
            $lblNotifMessage.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#1E3A8A')
            $lblNotifIcon.Text = "ℹ️"
        }
    }

    $lblNotifTitle.Text = $Title
    $lblNotifMessage.Text = $Message

    if (-not [string]::IsNullOrWhiteSpace($ActionText) -and $null -ne $ActionScript) {
        $btnNotifAction.Text = $ActionText
        $btnNotifAction.Tag = $ActionScript
        $btnNotifAction.Visible = $true
        $btnNotifAction.BackColor = $lblNotifTitle.ForeColor
        $btnNotifAction.ForeColor = [System.Drawing.Color]::White
    } else {
        $btnNotifAction.Visible = $false
        $btnNotifAction.Tag = $null
    }

    $pnlNotification.Visible = $true

    if ($null -ne $script:NotifTimer) {
        $script:NotifTimer.Stop()
        $script:NotifTimer.Dispose()
        $script:NotifTimer = $null
    }

    if ($AutoCloseSeconds -gt 0) {
        $script:NotifTimer = New-Object System.Windows.Forms.Timer
        $script:NotifTimer.Interval = ($AutoCloseSeconds * 1000)
        $script:NotifTimer.Add_Tick({
            Hide-NotificationBanner
        })
        $script:NotifTimer.Start()
    }
}

function Hide-NotificationBanner {
    if ($null -ne $pnlNotification -and -not $pnlNotification.IsDisposed) {
        $pnlNotification.Visible = $false
    }
    if ($null -ne $script:NotifTimer) {
        $script:NotifTimer.Stop()
        $script:NotifTimer.Dispose()
        $script:NotifTimer = $null
    }
}

# -----------------------------------------------------------------------------
# ESCANEO INTELIGENTE EN SEGUNDO PLANO Y AUTO-CONEXION USB -> WIFI
# -----------------------------------------------------------------------------
$script:UsbAutoScanEnabled = $true
$script:UsbAutoScanBusy = $false
$script:UsbProcessedSerials = @{}

function Start-UsbAutoWifiConversion {
    param([Parameter(Mandatory=$true)][string]$UsbSerial)

    if ($script:UsbProcessedSerials.ContainsKey($UsbSerial) -and $script:UsbProcessedSerials[$UsbSerial] -eq 'Converting') {
        return
    }
    $script:UsbProcessedSerials[$UsbSerial] = 'Converting'

    Write-LogEntry -Level 'INFO' -Message "⚡ Dispositivo USB detectado ($UsbSerial). Iniciando preparacion automatica para conexion WiFi..."
    Show-NotificationBanner -Type 'INFO' -Title "Dispositivo USB Detectado" -Message "Configurando puerto TCP/IP y obteniendo IP WiFi para $UsbSerial..." -AutoCloseSeconds 6

    $worker = {
        param($s)
        $info = [ordered]@{
            Success = $false
            UsbSerial = $s
            Ip = $null
            Model = $null
            WifiSerial = $null
            Error = $null
        }

        try {
            try {
                $m = @(Invoke-AdbCommand -Serial $s -Arguments @('shell','getprop','ro.product.model'))
                $info.Model = ($m -join ' ').Trim()
            } catch { }

            # 1. Habilitar TCP/IP 5555
            Invoke-AdbCommand -Serial $s -Arguments @('tcpip','5555') | Out-Null
            Start-Sleep -Milliseconds 800

            # 2. Descubrir IP WiFi
            $ipFound = $null
            $ipOut = @(Invoke-AdbCommand -Serial $s -Arguments @('shell','ip','-4','addr','show','wlan0'))
            foreach ($line in $ipOut) {
                if ($line -match 'inet\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)') {
                    $candidate = $Matches[1]
                    if ($candidate -ne '127.0.0.1') {
                        $ipFound = $candidate
                        break
                    }
                }
            }

            if (-not $ipFound) {
                $netcfg = @(Invoke-AdbCommand -Serial $s -Arguments @('shell','ifconfig','wlan0'))
                foreach ($line in $netcfg) {
                    if ($line -match 'inet addr:([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)') {
                        $ipFound = $Matches[1]
                        break
                    }
                }
            }

            if (-not $ipFound) {
                $info.Error = "No se pudo obtener la IP WiFi (wlan0). Asegurate de que el telefono este conectado a la misma red WiFi."
                return [pscustomobject]$info
            }

            $info.Ip = $ipFound
            $endpoint = "${ipFound}:5555"

            # 3. Conectar por WiFi
            $connOut = @(Invoke-AdbCommand -Arguments @('connect', $endpoint))
            $connText = $connOut -join ' '

            if ($connText -match '(connected to|already connected to)') {
                $info.Success = $true
                $info.WifiSerial = $endpoint
                
                # 4. Guardar en devices.json
                $friendly = if ($info.Model) { "$($info.Model) ($endpoint)" } else { "Android WiFi ($endpoint)" }
                Save-GuiAdbDevice -FriendlyName $friendly -Mode 'tcpip' -UsbSerial $s -LastEndpoint $endpoint -IsPaired $true | Out-Null
                
                # 5. Establecer como dispositivo activo
                Set-CurrentAdbDevice -Device ([pscustomobject]@{
                    Serial = $endpoint
                    FriendlyName = $friendly
                    LastEndpoint = $endpoint
                    UsbSerial = $s
                    Mode = 'tcpip'
                    Status = 'device'
                }) -Persist | Out-Null
            } else {
                $info.Error = "Fallo al conectar a $endpoint. Respuesta: $connText"
            }
        } catch {
            $info.Error = $_.Exception.Message
        }

        return [pscustomobject]$info
    }

    Invoke-GuiAsyncTask -Title "Auto-Conexion USB a WiFi ($UsbSerial)" -WorkerScript $worker -Parameters @{ s = $UsbSerial } -OnCompleted {
        param($res)
        if ($res -and $res.Success -and $res.Output -and $res.Output[0].Success) {
            $data = $res.Output[0]
            $script:UsbProcessedSerials[$UsbSerial] = 'Connected'
            
            Write-LogEntry -Level 'SUCCESS' -Message "🎉 Dispositivo conectado por WiFi con exito en $($data.WifiSerial). ¡Ya puedes desconectar el cable USB de forma segura!"
            
            Show-NotificationBanner -Type 'SUCCESS' `
                -Title "¡Conectado por WiFi! ($($data.WifiSerial))" `
                -Message "Se configuro y guardo automaticamente. YA PUEDES DESCONECTAR EL CABLE USB." `
                -ActionText "Verificar" `
                -ActionScript { Refresh-DeviceStatus } `
                -AutoCloseSeconds 12

            Refresh-DeviceStatus -Silent
        } else {
            $script:UsbProcessedSerials[$UsbSerial] = 'Failed'
            $errMsg = if ($res -and $res.Output -and $res.Output[0].Error) { $res.Output[0].Error } else { "No se pudo completar la auto-conexion." }
            Write-LogEntry -Level 'WARN' -Message "Auto-conexion USB -> WiFi incompleta: $errMsg"
            
            Show-NotificationBanner -Type 'WARN' `
                -Title "Conexion WiFi Incompleta" `
                -Message "$errMsg (El dispositivo sigue utilizable por USB)." `
                -ActionText "Reintentar" `
                -ActionScript { Start-UsbAutoWifiConversion -UsbSerial $UsbSerial } `
                -AutoCloseSeconds 10
            
            Refresh-DeviceStatus -Silent
        }
    }
}

function Invoke-UsbAutoDetection {
    if (-not $script:UsbAutoScanEnabled -or $script:UsbAutoScanBusy -or $script:RunningAsyncTasks -gt 0) {
        return
    }

    $script:UsbAutoScanBusy = $true
    try {
        if (-not (Get-Command adb -ErrorAction SilentlyContinue)) { return }
        
        $lines = @(adb devices 2>$null)
        foreach ($line in $lines) {
            $trimmed = $line.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('List of devices')) { continue }
            
            $parts = $trimmed -split '\s+'
            if ($parts.Count -lt 2) { continue }
            
            $serial = $parts[0]
            $status = $parts[1]

            # Ignorar si ya es conexion de red IP:PUERTO
            if ($serial -match '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$') {
                continue
            }

            if ($status -ceq 'unauthorized') {
                if (-not $script:UsbProcessedSerials.ContainsKey($serial) -or $script:UsbProcessedSerials[$serial] -ne 'UnauthorizedNotified') {
                    $script:UsbProcessedSerials[$serial] = 'UnauthorizedNotified'
                    Show-NotificationBanner -Type 'WARN' `
                        -Title "📱 Autorizacion Requerida en tu Telefono" `
                        -Message "Desbloquea la pantalla de tu telefono y presiona 'Permitir siempre la depuracion USB'." `
                        -ActionText "Ya lo permiti" `
                        -ActionScript {
                            $script:UsbProcessedSerials.Remove($serial)
                            Refresh-DeviceStatus -Silent
                        }
                    Write-LogEntry -Level 'WARN' -Message "Dispositivo USB '$serial' en estado UNAUTHORIZED. Requiere autorizacion en pantalla."
                }
                continue
            }

            if ($status -ceq 'device') {
                if (-not $script:UsbProcessedSerials.ContainsKey($serial) -or $script:UsbProcessedSerials[$serial] -eq 'UnauthorizedNotified') {
                    Start-UsbAutoWifiConversion -UsbSerial $serial
                }
            }
        }
    } catch {
        Write-LogEntry -Level 'WARN' -Message "Auto-Scanner USB: $($_.Exception.Message)"
    } finally {
        $script:UsbAutoScanBusy = $false
    }
}

# -----------------------------------------------------------------------------
# ACTUALIZACION COMPLETA DEL ESTADO DEL DISPOSITIVO Y CABECERA
# -----------------------------------------------------------------------------
function Refresh-DeviceStatus {
    param([switch]$Silent)

    if (-not $Silent) {
        Write-LogEntry -Level 'INFO' -Message 'Consultando estado de dispositivos...'
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
            AllDevices    = @()
            SavedDevices  = @()
        }

        if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
            $devInfo.FriendlyName = 'ADB no encontrado en PATH'
            return [pscustomobject]$devInfo
        }
        $devInfo.AdbAvailable = $true

        $onlineDevices = @(Get-AdbDevices)
        $savedDevices = @(Get-SavedAdbDevices)
        $devInfo.AllDevices = $onlineDevices
        $devInfo.SavedDevices = $savedDevices

        $cur = Get-CurrentAdbDevice

        if ($null -eq $cur -and $onlineDevices.Count -gt 0) {
            $candidate = @($onlineDevices | Where-Object { $_.Status -ceq 'device' })[0]
            if ($null -ne $candidate) {
                try {
                    $cur = Set-CurrentAdbDevice -Device $candidate
                } catch { }
            }
        }

        if ($null -ne $cur) {
            $devInfo.HasDevice = $true
            $devInfo.Serial = $cur.Serial
            $devInfo.FriendlyName = if ($cur.FriendlyName) { $cur.FriendlyName } else { $cur.Serial }
            $devInfo.Endpoint = if ($cur.LastEndpoint) { $cur.LastEndpoint } else { $cur.Serial }
            
            $record = @($onlineDevices | Where-Object { $_.Serial -ceq $cur.Serial })[0]
            $devInfo.Status = if ($null -ne $record) { [string]$record.Status } else { 'offline' }

            if ($devInfo.Status -ceq 'device') {
                try {
                    $m = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','getprop','ro.product.model'))
                    $devInfo.Model = ($m -join ' ').Trim()
                } catch { }
                try {
                    $v = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','getprop','ro.build.version.release'))
                    $devInfo.AndroidVer = ($v -join ' ').Trim()
                } catch { }
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
            $devInfo.FriendlyName = "Dispositivo disponible: $($first.Serial)"
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
            Show-NotificationBanner -Type 'ERROR' -Title "ADB no instalado" -Message "No se encontro el ejecutable 'adb.exe' en el sistema o PATH."
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

        # Actualizar Combo de Dispositivos en Cabecera
        Update-HeaderDeviceList -AllDevices $info.AllDevices -SavedDevices $info.SavedDevices -CurrentSerial $info.Serial
        
        # Actualizar lista de dispositivos en pestaña de Gestion
        Update-DevicesTabGrid -AllDevices $info.AllDevices -SavedDevices $info.SavedDevices
    }
}

function Update-HeaderDeviceList {
    param($AllDevices, $SavedDevices, [string]$CurrentSerial)
    
    if ($null -eq $cboHeaderDevices -or $cboHeaderDevices.IsDisposed) { return }
    
    $cboHeaderDevices.BeginUpdate()
    $cboHeaderDevices.Items.Clear()
    $script:HeaderDeviceMap = @()

    $allOnline = @($AllDevices)
    $allSaved = @($SavedDevices)

    foreach ($dev in $allOnline) {
        $savedMatch = @($allSaved | Where-Object { $_.UsbSerial -ceq $dev.Serial -or $_.LastEndpoint -ceq $dev.Serial })[0]
        $name = if ($savedMatch) { $savedMatch.FriendlyName } else { $dev.Serial }
        $display = "📱 $name ($($dev.Status))"
        $cboHeaderDevices.Items.Add($display) | Out-Null
        $script:HeaderDeviceMap += [pscustomobject]@{ Device = $dev; Display = $display; Serial = $dev.Serial; IsOnline = $true }
    }

    foreach ($s in $allSaved) {
        $alreadyInList = @($allOnline | Where-Object { $_.Serial -ceq $s.LastEndpoint -or $_.Serial -ceq $s.UsbSerial }).Count -gt 0
        if (-not $alreadyInList) {
            $display = "💾 $($s.FriendlyName) [Guardado]"
            $cboHeaderDevices.Items.Add($display) | Out-Null
            $script:HeaderDeviceMap += [pscustomobject]@{ Device = $s; Display = $display; Serial = if ($s.LastEndpoint) { $s.LastEndpoint } else { $s.UsbSerial }; IsOnline = $false }
        }
    }

    $selectedIndex = -1
    for ($i = 0; $i -lt $script:HeaderDeviceMap.Count; $i++) {
        if ($script:HeaderDeviceMap[$i].Serial -ceq $CurrentSerial) {
            $selectedIndex = $i
            break
        }
    }
    if ($selectedIndex -ge 0) {
        $cboHeaderDevices.SelectedIndex = $selectedIndex
    } elseif ($cboHeaderDevices.Items.Count -gt 0) {
        $cboHeaderDevices.SelectedIndex = 0
    }
    $cboHeaderDevices.EndUpdate()
}

# -----------------------------------------------------------------------------
# CONSTRUCCION DE LA VENTANA PRINCIPAL (WinForms)
# -----------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "Android ADB Tools - Control Center"
$form.Size = New-Object System.Drawing.Size(1260, 840)
$form.MinimumSize = New-Object System.Drawing.Size(1080, 720)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.WindowState = [System.Windows.Forms.FormWindowState]::Maximized
$form.BackColor = $script:Theme.BgWindow
$form.Font = $script:Fonts.Body

# =============================================================================
# 1. CABECERA PRINCIPAL (Top Header Panel)
# =============================================================================
$pnlHeader = New-Object System.Windows.Forms.Panel
$pnlHeader.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlHeader.Height = 96
$pnlHeader.BackColor = $script:Theme.BgHeader
$pnlHeader.Padding = New-Object System.Windows.Forms.Padding(16, 8, 16, 8)

$pnlHeaderBorder = New-Object System.Windows.Forms.Panel
$pnlHeaderBorder.Dock = [System.Windows.Forms.DockStyle]::Bottom
$pnlHeaderBorder.Height = 1
$pnlHeaderBorder.BackColor = $script:Theme.BorderCard
$pnlHeader.Controls.Add($pnlHeaderBorder)

# Panel Contenedor de la Derecha (Selector de Dispositivo y Botones)
$pnlHeaderRight = New-Object System.Windows.Forms.Panel
$pnlHeaderRight.Dock = [System.Windows.Forms.DockStyle]::Right
$pnlHeaderRight.Width = 630
$pnlHeaderRight.BackColor = $script:Theme.BgHeader

# Label explicito para el Selector de Dispositivos
$lblHeaderDevSelect = New-Object System.Windows.Forms.Label
$lblHeaderDevSelect.Text = "📱 Dispositivo Activo:"
$lblHeaderDevSelect.Font = $script:Fonts.SmallBold
$lblHeaderDevSelect.ForeColor = $script:Theme.TextTitle
$lblHeaderDevSelect.Location = New-Object System.Drawing.Point(0, 12)
$lblHeaderDevSelect.Size = New-Object System.Drawing.Size(140, 24)
$lblHeaderDevSelect.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$pnlHeaderRight.Controls.Add($lblHeaderDevSelect)

# Selector Rapido de Dispositivo (Visibilidad Garantizada)
$cboHeaderDevices = New-Object System.Windows.Forms.ComboBox
$cboHeaderDevices.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
$cboHeaderDevices.Font = $script:Fonts.Small
$cboHeaderDevices.Size = New-Object System.Drawing.Size(240, 26)
$cboHeaderDevices.Location = New-Object System.Drawing.Point(142, 11)
$cboHeaderDevices.Add_SelectedIndexChanged({
    if ($cboHeaderDevices.SelectedIndex -ge 0 -and $script:HeaderDeviceMap.Count -gt $cboHeaderDevices.SelectedIndex) {
        $selectedEntry = $script:HeaderDeviceMap[$cboHeaderDevices.SelectedIndex]
        if ($selectedEntry.Serial -and $selectedEntry.IsOnline) {
            try {
                Set-CurrentAdbDevice -Device $selectedEntry.Device -Persist | Out-Null
                Write-LogEntry -Level 'INFO' -Message "Dispositivo activo cambiado a: $($selectedEntry.Serial)"
            } catch { }
        }
    }
})
$pnlHeaderRight.Controls.Add($cboHeaderDevices)

# Boton Recargar
$btnHeaderRefresh = New-Object System.Windows.Forms.Button
$btnHeaderRefresh.Text = "🔄"
$btnHeaderRefresh.Font = $script:Fonts.TitleMid
$btnHeaderRefresh.Size = New-Object System.Drawing.Size(38, 28)
$btnHeaderRefresh.Location = New-Object System.Drawing.Point(388, 10)
$btnHeaderRefresh.BackColor = $script:Theme.Secondary
$btnHeaderRefresh.ForeColor = $script:Theme.SecondaryFg
$btnHeaderRefresh.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnHeaderRefresh.FlatAppearance.BorderSize = 1
$btnHeaderRefresh.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnHeaderRefresh.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnHeaderRefresh.Add_Click({ Refresh-DeviceStatus })
$pnlHeaderRight.Controls.Add($btnHeaderRefresh)

# Toggle Auto-Scan USB
$btnAutoScanToggle = New-Object System.Windows.Forms.Button
$btnAutoScanToggle.Text = "⚡ Auto-USB: ON"
$btnAutoScanToggle.Font = $script:Fonts.SmallBold
$btnAutoScanToggle.Size = New-Object System.Drawing.Size(120, 28)
$btnAutoScanToggle.Location = New-Object System.Drawing.Point(432, 10)
$btnAutoScanToggle.BackColor = $script:Theme.BadgeSuccessBg
$btnAutoScanToggle.ForeColor = $script:Theme.BadgeSuccessFg
$btnAutoScanToggle.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnAutoScanToggle.FlatAppearance.BorderSize = 0
$btnAutoScanToggle.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnAutoScanToggle.Add_Click({
    $script:UsbAutoScanEnabled = -not $script:UsbAutoScanEnabled
    if ($script:UsbAutoScanEnabled) {
        $btnAutoScanToggle.Text = "⚡ Auto-USB: ON"
        $btnAutoScanToggle.BackColor = $script:Theme.BadgeSuccessBg
        $btnAutoScanToggle.ForeColor = $script:Theme.BadgeSuccessFg
        Write-LogEntry -Level 'INFO' -Message 'Auto-deteccion USB activada.'
    } else {
        $btnAutoScanToggle.Text = "⚡ Auto-USB: OFF"
        $btnAutoScanToggle.BackColor = $script:Theme.BadgeMutedBg
        $btnAutoScanToggle.ForeColor = $script:Theme.BadgeMutedFg
        Write-LogEntry -Level 'INFO' -Message 'Auto-deteccion USB pausada.'
    }
})
$pnlHeaderRight.Controls.Add($btnAutoScanToggle)

# Boton Destacado para Abrir MODO AVANZADO
$btnOpenAdvFull = New-Object System.Windows.Forms.Button
$btnOpenAdvFull.Text = "⚡ Modo Avanzado (Consola)"
$btnOpenAdvFull.Font = $script:Fonts.SmallBold
$btnOpenAdvFull.Size = New-Object System.Drawing.Size(200, 32)
$btnOpenAdvFull.Location = New-Object System.Drawing.Point(352, 48)
$btnOpenAdvFull.BackColor = $script:Theme.Primary
$btnOpenAdvFull.ForeColor = $script:Theme.PrimaryFg
$btnOpenAdvFull.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnOpenAdvFull.FlatAppearance.BorderSize = 0
$btnOpenAdvFull.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnOpenAdvFull.Add_MouseEnter({ $btnOpenAdvFull.BackColor = $script:Theme.PrimaryHover })
$btnOpenAdvFull.Add_MouseLeave({ $btnOpenAdvFull.BackColor = $script:Theme.Primary })
$btnOpenAdvFull.Add_Click({ Show-AdvancedConsoleWindow })
$pnlHeaderRight.Controls.Add($btnOpenAdvFull)

$pnlHeader.Controls.Add($pnlHeaderRight)

# Informacion del Dispositivo (Panel Izquierdo)
$lblDeviceName = New-Object System.Windows.Forms.Label
$lblDeviceName.Text = "Iniciando Android ADB Tools..."
$lblDeviceName.Font = $script:Fonts.TitleLarge
$lblDeviceName.ForeColor = $script:Theme.TextTitle
$lblDeviceName.Location = New-Object System.Drawing.Point(20, 10)
$lblDeviceName.Size = New-Object System.Drawing.Size(460, 26)
$pnlHeader.Controls.Add($lblDeviceName)

$lblStatusBadge = New-Object System.Windows.Forms.Label
$lblStatusBadge.Text = "● BUSCANDO"
$lblStatusBadge.Font = $script:Fonts.Badge
$lblStatusBadge.BackColor = $script:Theme.BadgeMutedBg
$lblStatusBadge.ForeColor = $script:Theme.BadgeMutedFg
$lblStatusBadge.Location = New-Object System.Drawing.Point(22, 44)
$lblStatusBadge.Size = New-Object System.Drawing.Size(125, 24)
$lblStatusBadge.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$pnlHeader.Controls.Add($lblStatusBadge)

$lblEndpoint = New-Object System.Windows.Forms.Label
$lblEndpoint.Text = "🌐 Escaneando..."
$lblEndpoint.Font = $script:Fonts.SmallBold
$lblEndpoint.ForeColor = $script:Theme.TextBody
$lblEndpoint.Location = New-Object System.Drawing.Point(155, 46)
$lblEndpoint.Size = New-Object System.Drawing.Size(180, 20)
$pnlHeader.Controls.Add($lblEndpoint)

$lblBatteryTemp = New-Object System.Windows.Forms.Label
$lblBatteryTemp.Text = "🔋 Consultando..."
$lblBatteryTemp.Font = $script:Fonts.Small
$lblBatteryTemp.ForeColor = $script:Theme.TextMuted
$lblBatteryTemp.Location = New-Object System.Drawing.Point(340, 46)
$lblBatteryTemp.Size = New-Object System.Drawing.Size(220, 20)
$pnlHeader.Controls.Add($lblBatteryTemp)

$form.Controls.Add($pnlHeader)

# =============================================================================
# 2. BANNER DE NOTIFICACIONES INTERACTIVAS
# =============================================================================
$pnlNotification = New-Object System.Windows.Forms.Panel
$pnlNotification.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlNotification.Height = 60
$pnlNotification.BackColor = $script:Theme.BadgeInfoBg
$pnlNotification.Padding = New-Object System.Windows.Forms.Padding(16, 8, 16, 8)
$pnlNotification.Visible = $false

$lblNotifIcon = New-Object System.Windows.Forms.Label
$lblNotifIcon.Text = "ℹ️"
$lblNotifIcon.Font = $script:Fonts.TitleLarge
$lblNotifIcon.Location = New-Object System.Drawing.Point(16, 12)
$lblNotifIcon.Size = New-Object System.Drawing.Size(32, 32)
$pnlNotification.Controls.Add($lblNotifIcon)

$lblNotifTitle = New-Object System.Windows.Forms.Label
$lblNotifTitle.Text = "Notificacion del Sistema"
$lblNotifTitle.Font = $script:Fonts.TitleSmall
$lblNotifTitle.ForeColor = $script:Theme.BadgeInfoFg
$lblNotifTitle.Location = New-Object System.Drawing.Point(54, 8)
$lblNotifTitle.Size = New-Object System.Drawing.Size(800, 20)
$pnlNotification.Controls.Add($lblNotifTitle)

$lblNotifMessage = New-Object System.Windows.Forms.Label
$lblNotifMessage.Text = "Detalles del mensaje..."
$lblNotifMessage.Font = $script:Fonts.Small
$lblNotifMessage.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#065F46')
$lblNotifMessage.Location = New-Object System.Drawing.Point(54, 28)
$lblNotifMessage.Size = New-Object System.Drawing.Size(800, 24)
$pnlNotification.Controls.Add($lblNotifMessage)

$btnNotifAction = New-Object System.Windows.Forms.Button
$btnNotifAction.Text = "Accion"
$btnNotifAction.Font = $script:Fonts.SmallBold
$btnNotifAction.Size = New-Object System.Drawing.Size(130, 32)
$btnNotifAction.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnNotifAction.Location = New-Object System.Drawing.Point(1000, 14)
$btnNotifAction.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnNotifAction.FlatAppearance.BorderSize = 0
$btnNotifAction.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnNotifAction.Add_Click({
    if ($btnNotifAction.Tag -is [scriptblock]) {
        & $btnNotifAction.Tag
    }
})
$pnlNotification.Controls.Add($btnNotifAction)

$btnNotifClose = New-Object System.Windows.Forms.Button
$btnNotifClose.Text = "✕"
$btnNotifClose.Font = $script:Fonts.SmallBold
$btnNotifClose.Size = New-Object System.Drawing.Size(32, 32)
$btnNotifClose.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnNotifClose.Location = New-Object System.Drawing.Point(1140, 14)
$btnNotifClose.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnNotifClose.FlatAppearance.BorderSize = 0
$btnNotifClose.BackColor = [System.Drawing.Color]::Transparent
$btnNotifClose.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnNotifClose.Add_Click({ Hide-NotificationBanner })
$pnlNotification.Controls.Add($btnNotifClose)

$form.Controls.Add($pnlNotification)

# =============================================================================
# 3. CONTENEDOR CENTRAL: SIDEBAR IZQUIERDA + ESPACIO DE TRABAJO CON PESTANAS
# =============================================================================
$pnlCenter = New-Object System.Windows.Forms.Panel
$pnlCenter.Dock = [System.Windows.Forms.DockStyle]::Fill
$pnlCenter.Padding = New-Object System.Windows.Forms.Padding(16, 12, 16, 12)
$pnlCenter.BackColor = $script:Theme.BgWindow

# -----------------------------------------------------------------------------
# SIDEBAR IZQUIERDA - NAVEGACION RAPIDA Y HERRAMIENTAS
# -----------------------------------------------------------------------------
$cardSidebar = New-Object System.Windows.Forms.Panel
$cardSidebar.Dock = [System.Windows.Forms.DockStyle]::Left
$cardSidebar.Width = 265
$cardSidebar.BackColor = $script:Theme.BgCard
$cardSidebar.Padding = New-Object System.Windows.Forms.Padding(10)
$cardSidebar.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$cardSidebar.AutoScroll = $true

$lblSidebarHeader = New-Object System.Windows.Forms.Label
$lblSidebarHeader.Text = "HERRAMIENTAS & VISTAS"
$lblSidebarHeader.Font = $script:Fonts.TitleSmall
$lblSidebarHeader.ForeColor = $script:Theme.TextMuted
$lblSidebarHeader.Dock = [System.Windows.Forms.DockStyle]::Top
$lblSidebarHeader.Height = 24
$cardSidebar.Controls.Add($lblSidebarHeader)

$pnlSidebarButtons = New-Object System.Windows.Forms.FlowLayoutPanel
$pnlSidebarButtons.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlSidebarButtons.FlowDirection = [System.Windows.Forms.FlowDirection]::TopDown
$pnlSidebarButtons.WrapContents = $false
$pnlSidebarButtons.AutoSize = $true
$pnlSidebarButtons.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
$pnlSidebarButtons.Padding = New-Object System.Windows.Forms.Padding(0, 4, 0, 0)

function New-NavButton {
    param([string]$Icon, [string]$Title, [scriptblock]$OnClick, [bool]$IsHighlight = $false)
    
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = " $Icon  $Title"
    $btn.Font = $script:Fonts.BodyBold
    $btn.Size = New-Object System.Drawing.Size(226, 38)
    $btn.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 6)
    $btn.BackColor = if ($IsHighlight) { $script:Theme.Primary } else { $script:Theme.BtnActionBg }
    $btn.ForeColor = if ($IsHighlight) { $script:Theme.PrimaryFg } else { $script:Theme.BtnActionFg }
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.FlatAppearance.BorderSize = 1
    $btn.FlatAppearance.BorderColor = if ($IsHighlight) { $script:Theme.Primary } else { $script:Theme.BtnActionBorder }
    $btn.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
    $btn.Padding = New-Object System.Windows.Forms.Padding(8, 0, 0, 0)
    $btn.Cursor = [System.Windows.Forms.Cursors]::Hand
    
    $btn.Add_MouseEnter({
        param($sender, $eventArgs)
        $b = [System.Windows.Forms.Button]$sender
        if (-not $IsHighlight) {
            $b.BackColor = $script:Theme.BtnActionHover
            $b.FlatAppearance.BorderColor = [System.Drawing.ColorTranslator]::FromHtml('#93C5FD')
            $b.ForeColor = $script:Theme.Primary
        }
    })
    $btn.Add_MouseLeave({
        param($sender, $eventArgs)
        $b = [System.Windows.Forms.Button]$sender
        if (-not $IsHighlight) {
            $b.BackColor = $script:Theme.BtnActionBg
            $b.FlatAppearance.BorderColor = $script:Theme.BtnActionBorder
            $b.ForeColor = $script:Theme.BtnActionFg
        }
    })
    if ($OnClick) { $btn.Add_Click($OnClick) }
    return $btn
}

# Botones de la Barra Lateral
$btnNavExplorer  = New-NavButton -Icon "📁" -Title "Explorador Android" -OnClick { $tabControlMain.SelectedTab = $tabExplorer; Refresh-AndroidExplorer }
$btnNavTransfer  = New-NavButton -Icon "⇄"  -Title "Transferir Fotos & Datos" -OnClick { $tabControlMain.SelectedTab = $tabTransfer }
$btnNavDevices   = New-NavButton -Icon "📱" -Title "Gestion de Dispositivos" -OnClick { $tabControlMain.SelectedTab = $tabDevices; Refresh-DeviceStatus }
$btnNavApps      = New-NavButton -Icon "📦" -Title "Gestion de Aplicaciones" -OnClick { Show-AppsManagerDialog }
$btnNavScreen    = New-NavButton -Icon "📸" -Title "Captura / Espejo Pantalla" -OnClick { Show-ScreenDialog }
$btnNavClean     = New-NavButton -Icon "🧹" -Title "Limpieza de Cache" -OnClick { Invoke-DeviceCleanCaches }
$btnNavDiag      = New-NavButton -Icon "🩺" -Title "Diagnostico Completo" -OnClick { Invoke-FullDiagnostics }
$btnNavLogs      = New-NavButton -Icon "📜" -Title "Registro de Actividad" -OnClick { $tabControlMain.SelectedTab = $tabLogs }
$btnNavAdvanced  = New-NavButton -Icon "⚡" -Title "Modo Avanzado (Consola)" -OnClick { Show-AdvancedConsoleWindow } -IsHighlight $true

$pnlSidebarButtons.Controls.AddRange(@(
    $btnNavExplorer,
    $btnNavTransfer,
    $btnNavDevices,
    $btnNavApps,
    $btnNavScreen,
    $btnNavClean,
    $btnNavDiag,
    $btnNavLogs,
    $btnNavAdvanced
))
$cardSidebar.Controls.Add($pnlSidebarButtons)
$lblSidebarHeader.SendToBack()
$pnlCenter.Controls.Add($cardSidebar)

# Separador Vertical
$pnlSplitGap = New-Object System.Windows.Forms.Panel
$pnlSplitGap.Dock = [System.Windows.Forms.DockStyle]::Left
$pnlSplitGap.Width = 12
$pnlSplitGap.BackColor = $script:Theme.BgWindow
$pnlCenter.Controls.Add($pnlSplitGap)

# -----------------------------------------------------------------------------
# ESPACIO DE TRABAJO PRINCIPAL CON PESTAÑAS MODERNAS
# -----------------------------------------------------------------------------
$cardWorkspace = New-Object System.Windows.Forms.Panel
$cardWorkspace.Dock = [System.Windows.Forms.DockStyle]::Fill
$cardWorkspace.BackColor = $script:Theme.BgCard
$cardWorkspace.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$cardWorkspace.Padding = New-Object System.Windows.Forms.Padding(10)

$tabControlMain = New-Object System.Windows.Forms.TabControl
$tabControlMain.Dock = [System.Windows.Forms.DockStyle]::Fill
$tabControlMain.Font = $script:Fonts.TitleSmall
$tabControlMain.Padding = New-Object System.Drawing.Point(16, 8)

# =============================================================================
# PESTANA 1: 📁 EXPLORADOR DE ARCHIVOS ANDROID (Navegacion Visual)
# =============================================================================
$tabExplorer = New-Object System.Windows.Forms.TabPage
$tabExplorer.Text = "  📁 Explorador Android  "
$tabExplorer.BackColor = $script:Theme.BgCard
$tabExplorer.Padding = New-Object System.Windows.Forms.Padding(8)

# Barra de Ruta y Botones de Navegacion
$pnlExpTop = New-Object System.Windows.Forms.Panel
$pnlExpTop.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlExpTop.Height = 72
$pnlExpTop.BackColor = $script:Theme.BgCard

$lblExpPathLabel = New-Object System.Windows.Forms.Label
$lblExpPathLabel.Text = "Ruta Actual:"
$lblExpPathLabel.Font = $script:Fonts.SmallBold
$lblExpPathLabel.Location = New-Object System.Drawing.Point(2, 6)
$lblExpPathLabel.Size = New-Object System.Drawing.Size(80, 20)
$pnlExpTop.Controls.Add($lblExpPathLabel)

$txtExpCurrentPath = New-Object System.Windows.Forms.TextBox
$txtExpCurrentPath.Font = $script:Fonts.Code
$txtExpCurrentPath.Text = "/sdcard"
$txtExpCurrentPath.Location = New-Object System.Drawing.Point(82, 4)
$txtExpCurrentPath.Size = New-Object System.Drawing.Size(420, 24)
$txtExpCurrentPath.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
$txtExpCurrentPath.Add_KeyDown({
    param($s, $e)
    if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
        $e.SuppressKeyPress = $true
        Navigate-AndroidDirectory -Path $txtExpCurrentPath.Text.Trim()
    }
})
$pnlExpTop.Controls.Add($txtExpCurrentPath)

$btnExpGo = New-Object System.Windows.Forms.Button
$btnExpGo.Text = "Ir"
$btnExpGo.Font = $script:Fonts.SmallBold
$btnExpGo.Location = New-Object System.Drawing.Point(508, 3)
$btnExpGo.Size = New-Object System.Drawing.Size(45, 26)
$btnExpGo.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnExpGo.BackColor = $script:Theme.Secondary
$btnExpGo.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpGo.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpGo.Add_Click({ Navigate-AndroidDirectory -Path $txtExpCurrentPath.Text.Trim() })
$pnlExpTop.Controls.Add($btnExpGo)

$btnExpUp = New-Object System.Windows.Forms.Button
$btnExpUp.Text = "⬆ Subir Nivel"
$btnExpUp.Font = $script:Fonts.SmallBold
$btnExpUp.Location = New-Object System.Drawing.Point(558, 3)
$btnExpUp.Size = New-Object System.Drawing.Size(100, 26)
$btnExpUp.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnExpUp.BackColor = $script:Theme.Secondary
$btnExpUp.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpUp.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpUp.Add_Click({ Navigate-AndroidParentDirectory })
$pnlExpTop.Controls.Add($btnExpUp)

$btnExpRefresh = New-Object System.Windows.Forms.Button
$btnExpRefresh.Text = "🔄"
$btnExpRefresh.Font = $script:Fonts.SmallBold
$btnExpRefresh.Location = New-Object System.Drawing.Point(662, 3)
$btnExpRefresh.Size = New-Object System.Drawing.Size(36, 26)
$btnExpRefresh.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnExpRefresh.BackColor = $script:Theme.Secondary
$btnExpRefresh.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpRefresh.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpRefresh.Add_Click({ Refresh-AndroidExplorer })
$pnlExpTop.Controls.Add($btnExpRefresh)

# Accesos Rapidos a Carpetas Clave
$pnlQuickJumps = New-Object System.Windows.Forms.FlowLayoutPanel
$pnlQuickJumps.Location = New-Object System.Drawing.Point(0, 36)
$pnlQuickJumps.Size = New-Object System.Drawing.Size(700, 32)
$pnlQuickJumps.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right

function New-QuickJumpBtn {
    param([string]$Title, [string]$Target)
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Title
    $b.Font = $script:Fonts.Small
    $b.AutoSize = $true
    $b.Height = 26
    $b.Margin = New-Object System.Windows.Forms.Padding(0, 0, 6, 0)
    $b.BackColor = $script:Theme.Secondary
    $b.ForeColor = $script:Theme.SecondaryFg
    $b.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $b.FlatAppearance.BorderSize = 1
    $b.FlatAppearance.BorderColor = $script:Theme.BorderCard
    $b.Cursor = [System.Windows.Forms.Cursors]::Hand
    $b.Add_Click({ Navigate-AndroidDirectory -Path $Target })
    return $b
}

$pnlQuickJumps.Controls.Add((New-QuickJumpBtn -Title "🏠 /sdcard" -Target "/sdcard"))
$pnlQuickJumps.Controls.Add((New-QuickJumpBtn -Title "📸 DCIM (Fotos)" -Target "/sdcard/DCIM"))
$pnlQuickJumps.Controls.Add((New-QuickJumpBtn -Title "📥 Download" -Target "/sdcard/Download"))
$pnlQuickJumps.Controls.Add((New-QuickJumpBtn -Title "📄 Documents" -Target "/sdcard/Documents"))
$pnlQuickJumps.Controls.Add((New-QuickJumpBtn -Title "🖼️ Pictures" -Target "/sdcard/Pictures"))
$pnlQuickJumps.Controls.Add((New-QuickJumpBtn -Title "🎵 Music" -Target "/sdcard/Music"))
$pnlExpTop.Controls.Add($pnlQuickJumps)

$tabExplorer.Controls.Add($pnlExpTop)

# Lista de Archivos y Carpetas (ListView)
$lvFiles = New-Object System.Windows.Forms.ListView
$lvFiles.Dock = [System.Windows.Forms.DockStyle]::Fill
$lvFiles.View = [System.Windows.Forms.View]::Details
$lvFiles.FullRowSelect = $true
$lvFiles.GridLines = $true
$lvFiles.Font = $script:Fonts.Body
$lvFiles.BackColor = [System.Drawing.Color]::White
$lvFiles.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$lvFiles.Columns.Add("Nombre", 340) | Out-Null
$lvFiles.Columns.Add("Tipo", 110) | Out-Null
$lvFiles.Columns.Add("Tamano", 110) | Out-Null
$lvFiles.Columns.Add("Ruta Completa", 220) | Out-Null

$lvFiles.Add_DoubleClick({
    if ($lvFiles.SelectedItems.Count -gt 0) {
        $item = $lvFiles.SelectedItems[0]
        $isDir = [bool]$item.Tag.IsDir
        $path = [string]$item.Tag.Path
        if ($isDir) {
            Navigate-AndroidDirectory -Path $path
        } else {
            Download-SelectedFile -AndroidPath $path
        }
    }
})
$tabExplorer.Controls.Add($lvFiles)

# Barra Inferior de Acciones del Explorador
$pnlExpBottom = New-Object System.Windows.Forms.Panel
$pnlExpBottom.Dock = [System.Windows.Forms.DockStyle]::Bottom
$pnlExpBottom.Height = 44
$pnlExpBottom.Padding = New-Object System.Windows.Forms.Padding(0, 8, 0, 0)
$pnlExpBottom.BackColor = $script:Theme.BgCard

$btnExpDownload = New-Object System.Windows.Forms.Button
$btnExpDownload.Text = "📥 Descargar Seleccion"
$btnExpDownload.Font = $script:Fonts.SmallBold
$btnExpDownload.BackColor = $script:Theme.Primary
$btnExpDownload.ForeColor = $script:Theme.PrimaryFg
$btnExpDownload.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpDownload.FlatAppearance.BorderSize = 0
$btnExpDownload.Size = New-Object System.Drawing.Size(165, 30)
$btnExpDownload.Location = New-Object System.Drawing.Point(0, 8)
$btnExpDownload.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpDownload.Add_Click({
    if ($lvFiles.SelectedItems.Count -gt 0) {
        $item = $lvFiles.SelectedItems[0]
        Download-SelectedFile -AndroidPath $item.Tag.Path
    } else {
        [System.Windows.Forms.MessageBox]::Show("Selecciona un archivo o carpeta para descargar.", "Aviso", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
    }
})
$pnlExpBottom.Controls.Add($btnExpDownload)

$btnExpUpload = New-Object System.Windows.Forms.Button
$btnExpUpload.Text = "📤 Subir Archivo a esta Carpeta"
$btnExpUpload.Font = $script:Fonts.SmallBold
$btnExpUpload.BackColor = $script:Theme.Secondary
$btnExpUpload.ForeColor = $script:Theme.SecondaryFg
$btnExpUpload.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpUpload.FlatAppearance.BorderSize = 1
$btnExpUpload.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnExpUpload.Size = New-Object System.Drawing.Size(210, 30)
$btnExpUpload.Location = New-Object System.Drawing.Point(175, 8)
$btnExpUpload.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpUpload.Add_Click({ Upload-FileToCurrentAndroidPath })
$pnlExpBottom.Controls.Add($btnExpUpload)

$btnExpNewFolder = New-Object System.Windows.Forms.Button
$btnExpNewFolder.Text = "➕ Nueva Carpeta"
$btnExpNewFolder.Font = $script:Fonts.Small
$btnExpNewFolder.BackColor = $script:Theme.Secondary
$btnExpNewFolder.ForeColor = $script:Theme.SecondaryFg
$btnExpNewFolder.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpNewFolder.FlatAppearance.BorderSize = 1
$btnExpNewFolder.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnExpNewFolder.Size = New-Object System.Drawing.Size(125, 30)
$btnExpNewFolder.Location = New-Object System.Drawing.Point(395, 8)
$btnExpNewFolder.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpNewFolder.Add_Click({ Create-AndroidFolderPrompt })
$pnlExpBottom.Controls.Add($btnExpNewFolder)

$btnExpDelete = New-Object System.Windows.Forms.Button
$btnExpDelete.Text = "🗑️ Eliminar"
$btnExpDelete.Font = $script:Fonts.Small
$btnExpDelete.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FEE2E2')
$btnExpDelete.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
$btnExpDelete.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnExpDelete.FlatAppearance.BorderSize = 0
$btnExpDelete.Size = New-Object System.Drawing.Size(95, 30)
$btnExpDelete.Location = New-Object System.Drawing.Point(530, 8)
$btnExpDelete.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnExpDelete.Add_Click({ Delete-SelectedAndroidItem })
$pnlExpBottom.Controls.Add($btnExpDelete)

$tabExplorer.Controls.Add($pnlExpBottom)
$tabControlMain.TabPages.Add($tabExplorer)

# =============================================================================
# PESTANA 2: ⇄ TRANSFERENCIAS RAPIDAS Y FOTOS (Centro de Datos y Progreso)
# =============================================================================
$tabTransfer = New-Object System.Windows.Forms.TabPage
$tabTransfer.Text = "  ⇄ Transferencias & Fotos  "
$tabTransfer.BackColor = $script:Theme.BgCard
$tabTransfer.Padding = New-Object System.Windows.Forms.Padding(8)

$pnlTransferScroll = New-Object System.Windows.Forms.Panel
$pnlTransferScroll.Dock = [System.Windows.Forms.DockStyle]::Fill
$pnlTransferScroll.AutoScroll = $true
$pnlTransferScroll.Padding = New-Object System.Windows.Forms.Padding(10)

# Encabezado de la pestaña
$pnlTransHead = New-Object System.Windows.Forms.Panel
$pnlTransHead.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlTransHead.Height = 48

$lblTransferHeader = New-Object System.Windows.Forms.Label
$lblTransferHeader.Text = "Transferencia Inteligente de Fotos, Documentos y Medios"
$lblTransferHeader.Font = $script:Fonts.TitleMid
$lblTransferHeader.ForeColor = $script:Theme.TextTitle
$lblTransferHeader.Location = New-Object System.Drawing.Point(0, 0)
$lblTransferHeader.Size = New-Object System.Drawing.Size(650, 22)
$pnlTransHead.Controls.Add($lblTransferHeader)

$lblTransferSub = New-Object System.Windows.Forms.Label
$lblTransferSub.Text = "Descarga de fotos con 1 clic a tu PC sin necesidad de buscarlas manualmente entre carpetas."
$lblTransferSub.Font = $script:Fonts.Small
$lblTransferSub.ForeColor = $script:Theme.TextMuted
$lblTransferSub.Location = New-Object System.Drawing.Point(0, 24)
$lblTransferSub.Size = New-Object System.Drawing.Size(650, 20)
$pnlTransHead.Controls.Add($lblTransferSub)

$pnlTransferScroll.Controls.Add($pnlTransHead)

# Contenedor de Tarjetas de Transferencia con acomodo fluido Dock=Top
$pnlTransferCards = New-Object System.Windows.Forms.FlowLayoutPanel
$pnlTransferCards.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlTransferCards.FlowDirection = [System.Windows.Forms.FlowDirection]::LeftToRight
$pnlTransferCards.WrapContents = $true
$pnlTransferCards.AutoSize = $true
$pnlTransferCards.AutoSizeMode = [System.Windows.Forms.AutoSizeMode]::GrowAndShrink
$pnlTransferCards.Padding = New-Object System.Windows.Forms.Padding(0, 8, 0, 8)

function New-TransferCard {
    param([string]$Icon, [string]$Title, [string]$Desc, [string]$BtnText, [scriptblock]$OnClick)
    
    $p = New-Object System.Windows.Forms.Panel
    $p.Size = New-Object System.Drawing.Size(365, 88)
    $p.Margin = New-Object System.Windows.Forms.Padding(0, 0, 12, 12)
    $p.BackColor = $script:Theme.BgWindow
    $p.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $p.Padding = New-Object System.Windows.Forms.Padding(8)
    
    $lIcon = New-Object System.Windows.Forms.Label
    $lIcon.Text = $Icon
    $lIcon.Font = $script:Fonts.TitleLarge
    $lIcon.Location = New-Object System.Drawing.Point(8, 8)
    $lIcon.Size = New-Object System.Drawing.Size(34, 34)
    $p.Controls.Add($lIcon)
    
    $lTitle = New-Object System.Windows.Forms.Label
    $lTitle.Text = $Title
    $lTitle.Font = $script:Fonts.BodyBold
    $lTitle.ForeColor = $script:Theme.TextTitle
    $lTitle.Location = New-Object System.Drawing.Point(46, 6)
    $lTitle.Size = New-Object System.Drawing.Size(305, 20)
    $p.Controls.Add($lTitle)
    
    $lDesc = New-Object System.Windows.Forms.Label
    $lDesc.Text = $Desc
    $lDesc.Font = $script:Fonts.Small
    $lDesc.ForeColor = $script:Theme.TextMuted
    $lDesc.Location = New-Object System.Drawing.Point(46, 26)
    $lDesc.Size = New-Object System.Drawing.Size(205, 36)
    $p.Controls.Add($lDesc)
    
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $BtnText
    $b.Font = $script:Fonts.SmallBold
    $b.Size = New-Object System.Drawing.Size(95, 28)
    $b.Location = New-Object System.Drawing.Point(258, 44)
    $b.BackColor = $script:Theme.Primary
    $b.ForeColor = $script:Theme.PrimaryFg
    $b.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $b.FlatAppearance.BorderSize = 0
    $b.Cursor = [System.Windows.Forms.Cursors]::Hand
    if ($OnClick) { $b.Add_Click($OnClick) }
    $p.Controls.Add($b)
    
    return $p
}

# Tarjetas
$cardPhotosRecent = New-TransferCard -Icon "📸" -Title "Fotos Recientes (7 dias)" -Desc "Descarga solo las fotos tomadas en la ultima semana." -BtnText "Descargar" -OnClick { Invoke-TransferTask -Type 'RecentPhotos' }
$cardPhotosAll    = New-TransferCard -Icon "🖼️" -Title "Todas las Fotos (DCIM)" -Desc "Copia completa de la camara a Descargas\AndroidBackup." -BtnText "Descargar" -OnClick { Invoke-TransferTask -Type 'AllPhotos' }
$cardDownloads    = New-TransferCard -Icon "📥" -Title "Descargas (Download)" -Desc "Trae todos los archivos de la carpeta Download del telefono." -BtnText "Descargar" -OnClick { Invoke-TransferTask -Type 'Downloads' }
$cardDocuments    = New-TransferCard -Icon "📄" -Title "Documentos & PDFs" -Desc "Descarga archivos PDF y documentos almacenados." -BtnText "Descargar" -OnClick { Invoke-TransferTask -Type 'Documents' }
$cardUpload       = New-TransferCard -Icon "📤" -Title "Subir Archivo al Telefono" -Desc "Selecciona un archivo de tu PC y subelo a /sdcard/Download/." -BtnText "Subir..." -OnClick { Invoke-TransferTask -Type 'Upload' }
$cardFullBackup   = New-TransferCard -Icon "💾" -Title "Backup Completo de Medios" -Desc "Copia en paralelo DCIM, Downloads, Documents y Pictures." -BtnText "Iniciar" -OnClick { Invoke-TransferTask -Type 'FullBackup' }

$pnlTransferCards.Controls.AddRange(@(
    $cardPhotosRecent,
    $cardPhotosAll,
    $cardDownloads,
    $cardDocuments,
    $cardUpload,
    $cardFullBackup
))
$pnlTransferScroll.Controls.Add($pnlTransferCards)

# Zona de Progreso y Estado de Transferencia (Apilado Dock=Top justo debajo de las tarjetas)
$pnlTransferStatus = New-Object System.Windows.Forms.Panel
$pnlTransferStatus.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlTransferStatus.Height = 150
$pnlTransferStatus.BackColor = $script:Theme.BgWindow
$pnlTransferStatus.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$pnlTransferStatus.Padding = New-Object System.Windows.Forms.Padding(12)

$lblTransferStatusTitle = New-Object System.Windows.Forms.Label
$lblTransferStatusTitle.Text = "Estado de Transferencia:"
$lblTransferStatusTitle.Font = $script:Fonts.BodyBold
$lblTransferStatusTitle.ForeColor = $script:Theme.TextTitle
$lblTransferStatusTitle.Location = New-Object System.Drawing.Point(12, 10)
$lblTransferStatusTitle.Size = New-Object System.Drawing.Size(200, 20)
$pnlTransferStatus.Controls.Add($lblTransferStatusTitle)

$lblTransferDetails = New-Object System.Windows.Forms.Label
$lblTransferDetails.Text = "Sin transferencias activas. Selecciona una opcion arriba para comenzar."
$lblTransferDetails.Font = $script:Fonts.Small
$lblTransferDetails.ForeColor = $script:Theme.TextMuted
$lblTransferDetails.Location = New-Object System.Drawing.Point(12, 34)
$lblTransferDetails.Size = New-Object System.Drawing.Size(650, 24)
$pnlTransferStatus.Controls.Add($lblTransferDetails)

$pbTransferProgress = New-Object System.Windows.Forms.ProgressBar
$pbTransferProgress.Location = New-Object System.Drawing.Point(12, 62)
$pbTransferProgress.Size = New-Object System.Drawing.Size(700, 16)
$pbTransferProgress.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
$pbTransferProgress.Visible = $false
$pnlTransferStatus.Controls.Add($pbTransferProgress)

$btnOpenLocalDir = New-Object System.Windows.Forms.Button
$btnOpenLocalDir.Text = "📂 Abrir Carpeta en PC"
$btnOpenLocalDir.Font = $script:Fonts.SmallBold
$btnOpenLocalDir.BackColor = $script:Theme.Secondary
$btnOpenLocalDir.ForeColor = $script:Theme.SecondaryFg
$btnOpenLocalDir.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnOpenLocalDir.FlatAppearance.BorderSize = 1
$btnOpenLocalDir.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnOpenLocalDir.Size = New-Object System.Drawing.Size(180, 28)
$btnOpenLocalDir.Location = New-Object System.Drawing.Point(12, 90)
$btnOpenLocalDir.Visible = $false
$btnOpenLocalDir.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnOpenLocalDir.Add_Click({
    if ($btnOpenLocalDir.Tag -and (Test-Path -LiteralPath $btnOpenLocalDir.Tag)) {
        Start-Process -FilePath 'explorer.exe' -ArgumentList $btnOpenLocalDir.Tag
    }
})
$pnlTransferStatus.Controls.Add($btnOpenLocalDir)

$pnlTransferScroll.Controls.Add($pnlTransferStatus)

# Asegurar orden Z de arriba a abajo: Header -> Cards -> Status
$pnlTransHead.SendToBack()
$pnlTransferCards.BringToFront()
$pnlTransferStatus.BringToFront()

$tabTransfer.Controls.Add($pnlTransferScroll)
$tabControlMain.TabPages.Add($tabTransfer)

# =============================================================================
# PESTANA 3: 📱 GESTION DE DISPOSITIVOS Y RED
# =============================================================================
$tabDevices = New-Object System.Windows.Forms.TabPage
$tabDevices.Text = "  📱 Dispositivos & Red  "
$tabDevices.BackColor = $script:Theme.BgCard
$tabDevices.Padding = New-Object System.Windows.Forms.Padding(12)

$pnlDevTop = New-Object System.Windows.Forms.Panel
$pnlDevTop.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlDevTop.Height = 44

$lblDevListTitle = New-Object System.Windows.Forms.Label
$lblDevListTitle.Text = "Dispositivos Conectados y Guardados"
$lblDevListTitle.Font = $script:Fonts.TitleMid
$lblDevListTitle.ForeColor = $script:Theme.TextTitle
$lblDevListTitle.Location = New-Object System.Drawing.Point(0, 10)
$lblDevListTitle.Size = New-Object System.Drawing.Size(350, 24)
$pnlDevTop.Controls.Add($lblDevListTitle)

$btnDevConnectNew = New-Object System.Windows.Forms.Button
$btnDevConnectNew.Text = "✨ Conectar Nuevo Dispositivo"
$btnDevConnectNew.Font = $script:Fonts.SmallBold
$btnDevConnectNew.BackColor = $script:Theme.Primary
$btnDevConnectNew.ForeColor = $script:Theme.PrimaryFg
$btnDevConnectNew.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnDevConnectNew.FlatAppearance.BorderSize = 0
$btnDevConnectNew.Size = New-Object System.Drawing.Size(210, 30)
$btnDevConnectNew.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnDevConnectNew.Location = New-Object System.Drawing.Point(640, 6)
$btnDevConnectNew.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnDevConnectNew.Add_Click({ Show-NewDeviceWizardDialog })
$pnlDevTop.Controls.Add($btnDevConnectNew)

$tabDevices.Controls.Add($pnlDevTop)

$lvDevicesTab = New-Object System.Windows.Forms.ListView
$lvDevicesTab.Dock = [System.Windows.Forms.DockStyle]::Fill
$lvDevicesTab.View = [System.Windows.Forms.View]::Details
$lvDevicesTab.FullRowSelect = $true
$lvDevicesTab.GridLines = $true
$lvDevicesTab.Font = $script:Fonts.Body
$lvDevicesTab.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$lvDevicesTab.Columns.Add("Dispositivo / Nombre", 240) | Out-Null
$lvDevicesTab.Columns.Add("Serial / Endpoint", 200) | Out-Null
$lvDevicesTab.Columns.Add("Modo", 110) | Out-Null
$lvDevicesTab.Columns.Add("Estado", 130) | Out-Null
$tabDevices.Controls.Add($lvDevicesTab)

$pnlDevBottom = New-Object System.Windows.Forms.Panel
$pnlDevBottom.Dock = [System.Windows.Forms.DockStyle]::Bottom
$pnlDevBottom.Height = 46
$pnlDevBottom.Padding = New-Object System.Windows.Forms.Padding(0, 8, 0, 0)

$btnDevSetActive = New-Object System.Windows.Forms.Button
$btnDevSetActive.Text = "⭐ Usar como Activo"
$btnDevSetActive.Font = $script:Fonts.SmallBold
$btnDevSetActive.BackColor = $script:Theme.Primary
$btnDevSetActive.ForeColor = $script:Theme.PrimaryFg
$btnDevSetActive.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnDevSetActive.FlatAppearance.BorderSize = 0
$btnDevSetActive.Size = New-Object System.Drawing.Size(160, 30)
$btnDevSetActive.Location = New-Object System.Drawing.Point(0, 8)
$btnDevSetActive.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnDevSetActive.Add_Click({
    if ($lvDevicesTab.SelectedItems.Count -gt 0) {
        $item = $lvDevicesTab.SelectedItems[0]
        $devObj = $item.Tag
        if ($devObj) {
            Set-CurrentAdbDevice -Device $devObj -Persist | Out-Null
            Write-LogEntry -Level 'SUCCESS' -Message "Dispositivo activo cambiado a: $($devObj.Serial)"
            Refresh-DeviceStatus
        }
    }
})
$pnlDevBottom.Controls.Add($btnDevSetActive)

$btnDevConvertToWifi = New-Object System.Windows.Forms.Button
$btnDevConvertToWifi.Text = "⚡ Convertir USB a WiFi"
$btnDevConvertToWifi.Font = $script:Fonts.SmallBold
$btnDevConvertToWifi.BackColor = $script:Theme.Secondary
$btnDevConvertToWifi.ForeColor = $script:Theme.SecondaryFg
$btnDevConvertToWifi.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnDevConvertToWifi.FlatAppearance.BorderSize = 1
$btnDevConvertToWifi.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnDevConvertToWifi.Size = New-Object System.Drawing.Size(180, 30)
$btnDevConvertToWifi.Location = New-Object System.Drawing.Point(170, 8)
$btnDevConvertToWifi.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnDevConvertToWifi.Add_Click({
    if ($lvDevicesTab.SelectedItems.Count -gt 0) {
        $item = $lvDevicesTab.SelectedItems[0]
        $serial = $item.SubItems[1].Text
        if ($serial -and -not ($serial -match ':[0-9]+$')) {
            Start-UsbAutoWifiConversion -UsbSerial $serial
        } else {
            [System.Windows.Forms.MessageBox]::Show("Selecciona un dispositivo conectado por USB para convertir a WiFi.", "Aviso", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
        }
    }
})
$pnlDevBottom.Controls.Add($btnDevConvertToWifi)

$btnDevPing = New-Object System.Windows.Forms.Button
$btnDevPing.Text = "🌐 Probar Ping"
$btnDevPing.Font = $script:Fonts.Small
$btnDevPing.BackColor = $script:Theme.Secondary
$btnDevPing.ForeColor = $script:Theme.SecondaryFg
$btnDevPing.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnDevPing.FlatAppearance.BorderSize = 1
$btnDevPing.FlatAppearance.BorderColor = $script:Theme.BorderCard
$btnDevPing.Size = New-Object System.Drawing.Size(120, 30)
$btnDevPing.Location = New-Object System.Drawing.Point(360, 8)
$btnDevPing.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnDevPing.Add_Click({
    Invoke-GuiAsyncTask -Title "Ping a Dispositivo" -WorkerScript {
        $cur = Get-CurrentAdbDevice
        if ($cur.LastEndpoint -match '^([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)') {
            Test-Connection -ComputerName $Matches[1] -Count 2
        } else {
            throw "El dispositivo seleccionado no tiene direccion IP de red."
        }
    }
})
$pnlDevBottom.Controls.Add($btnDevPing)

$tabDevices.Controls.Add($pnlDevBottom)
$tabControlMain.TabPages.Add($tabDevices)

# =============================================================================
# PESTANA 4: 📜 REGISTRO Y LOGS EN VIVO
# =============================================================================
$tabLogs = New-Object System.Windows.Forms.TabPage
$tabLogs.Text = "  📜 Registro & Salida  "
$tabLogs.BackColor = $script:Theme.BgCard
$tabLogs.Padding = New-Object System.Windows.Forms.Padding(8)

$pnlLogTop = New-Object System.Windows.Forms.Panel
$pnlLogTop.Dock = [System.Windows.Forms.DockStyle]::Top
$pnlLogTop.Height = 36
$pnlLogTop.BackColor = $script:Theme.BgCard

$lblLogTitle = New-Object System.Windows.Forms.Label
$lblLogTitle.Text = "Registro de Actividad y Comandos ADB"
$lblLogTitle.Font = $script:Fonts.TitleMid
$lblLogTitle.ForeColor = $script:Theme.TextTitle
$lblLogTitle.Location = New-Object System.Drawing.Point(0, 6)
$lblLogTitle.Size = New-Object System.Drawing.Size(300, 24)
$pnlLogTop.Controls.Add($lblLogTitle)

$lblLastOp = New-Object System.Windows.Forms.Label
$lblLastOp.Text = "Listo"
$lblLastOp.Font = $script:Fonts.Small
$lblLastOp.ForeColor = $script:Theme.TextMuted
$lblLastOp.Location = New-Object System.Drawing.Point(310, 9)
$lblLastOp.Size = New-Object System.Drawing.Size(260, 20)
$pnlLogTop.Controls.Add($lblLastOp)

$btnLogClear = New-Object System.Windows.Forms.Button
$btnLogClear.Text = "🧹 Limpiar"
$btnLogClear.Font = $script:Fonts.Small
$btnLogClear.Size = New-Object System.Drawing.Size(80, 26)
$btnLogClear.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnLogClear.Location = New-Object System.Drawing.Point(620, 4)
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
$btnLogCopy.Location = New-Object System.Drawing.Point(705, 4)
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
$btnLogFile.Text = "📄 Ver .log"
$btnLogFile.Font = $script:Fonts.Small
$btnLogFile.Size = New-Object System.Drawing.Size(95, 26)
$btnLogFile.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
$btnLogFile.Location = New-Object System.Drawing.Point(785, 4)
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
        }
    } catch { }
})
$pnlLogTop.Controls.Add($btnLogFile)

$tabLogs.Controls.Add($pnlLogTop)

$pbProgress = New-Object System.Windows.Forms.ProgressBar
$pbProgress.Dock = [System.Windows.Forms.DockStyle]::Top
$pbProgress.Height = 3
$pbProgress.Visible = $false
$tabLogs.Controls.Add($pbProgress)

$txtLog = New-Object System.Windows.Forms.RichTextBox
$txtLog.Dock = [System.Windows.Forms.DockStyle]::Fill
$txtLog.BackColor = $script:Theme.LogBg
$txtLog.ForeColor = $script:Theme.LogFg
$txtLog.Font = $script:Fonts.Code
$txtLog.ReadOnly = $true
$txtLog.BorderStyle = [System.Windows.Forms.BorderStyle]::None
$tabLogs.Controls.Add($txtLog)

$tabControlMain.TabPages.Add($tabLogs)

$cardWorkspace.Controls.Add($tabControlMain)
$pnlCenter.Controls.Add($cardWorkspace)

# Z-Order de pnlCenter: SplitGap y CardSidebar se acoplan a la izquierda primero
$pnlSplitGap.SendToBack()
$cardSidebar.SendToBack()

# Formulario: Header y Notificacion se acoplan arriba antes del centro
$form.Controls.Add($pnlCenter)
$pnlNotification.SendToBack()
$pnlHeader.SendToBack()

# =============================================================================
# LOGICA DEL EXPLORADOR DE ARCHIVOS ANDROID
# =============================================================================
function Navigate-AndroidDirectory {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { $Path = '/sdcard' }
    $txtExpCurrentPath.Text = $Path
    Refresh-AndroidExplorer
}

function Navigate-AndroidParentDirectory {
    $cur = $txtExpCurrentPath.Text.Trim()
    if ($cur -eq '/' -or $cur -eq '/sdcard' -or [string]::IsNullOrWhiteSpace($cur)) {
        Navigate-AndroidDirectory -Path '/sdcard'
        return
    }
    $parent = Split-Path -Path $cur -Parent
    if ([string]::IsNullOrWhiteSpace($parent)) { $parent = '/' }
    Navigate-AndroidDirectory -Path ($parent -replace '\\', '/')
}

function Refresh-AndroidExplorer {
    $path = $txtExpCurrentPath.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($path)) { $path = '/sdcard' }

    Write-LogEntry -Level 'INFO' -Message "Explorador Android: Listando carpeta '$path'..."
    
    $worker = {
        param($dirPath)
        $curDev = Get-CurrentAdbDevice
        if ($null -eq $curDev -or $curDev.Status -cne 'device') {
            throw "No hay ningun dispositivo conectado y listo."
        }

        # Listar contenido mediante script seguro
        $items = @()
        $findLines = @(Invoke-AdbCommand -Serial $curDev.Serial -Arguments @('shell','find', $dirPath, '-mindepth', '1', '-maxdepth', '1', '-print'))
        
        foreach ($line in $findLines) {
            $entryPath = ([string]$line).Trim()
            if ([string]::IsNullOrWhiteSpace($entryPath)) { continue }
            $name = Split-Path -Path $entryPath -Leaf
            
            $isDir = $false
            try {
                Invoke-AdbCommand -Serial $curDev.Serial -Arguments @('shell', 'test', '-d', $entryPath) | Out-Null
                $isDir = $true
            } catch { }

            $size = 0L
            if (-not $isDir) {
                try {
                    $sizeText = (Invoke-AdbCommand -Serial $curDev.Serial -Arguments @('shell', 'stat', '-c', '%s', $entryPath)) -join ''
                    [void][long]::TryParse($sizeText.Trim(), [ref]$size)
                } catch { }
            }

            $items += [pscustomobject]@{
                Name = $name
                IsDir = $isDir
                Path = $entryPath
                SizeBytes = $size
            }
        }

        return $items
    }

    Invoke-GuiAsyncTask -Title "Explorador Android ($path)" -WorkerScript $worker -Parameters @{ dirPath = $path } -OnCompleted {
        param($res)
        if ($null -eq $res -or -not $res.Success) {
            Show-NotificationBanner -Type 'ERROR' -Title "Error en Explorador" -Message "No se pudo leer la carpeta '$path': $($res.Error)"
            return
        }

        $lvFiles.BeginUpdate()
        $lvFiles.Items.Clear()

        # Ordenar: Carpetas primero, luego archivos alfabetico
        $dirs = @($res.Output | Where-Object { $_.IsDir } | Sort-Object Name)
        $files = @($res.Output | Where-Object { -not $_.IsDir } | Sort-Object Name)

        foreach ($d in $dirs) {
            $lvi = New-Object System.Windows.Forms.ListViewItem("📁 $($d.Name)")
            $lvi.SubItems.Add("Carpeta") | Out-Null
            $lvi.SubItems.Add("--") | Out-Null
            $lvi.SubItems.Add($d.Path) | Out-Null
            $lvi.Tag = $d
            $lvi.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#1E40AF')
            $lvFiles.Items.Add($lvi) | Out-Null
        }

        foreach ($f in $files) {
            $icon = if ($f.Name -match '\.(jpg|jpeg|png|gif|webp|mp4|mkv)$') { "🖼️" } elseif ($f.Name -match '\.(pdf|doc|docx|txt)$') { "📄" } elseif ($f.Name -match '\.(mp3|wav|flac|m4a)$') { "🎵" } elseif ($f.Name -match '\.apk$') { "📦" } else { "📄" }
            $lvi = New-Object System.Windows.Forms.ListViewItem("$icon $($f.Name)")
            $lvi.SubItems.Add("Archivo") | Out-Null
            $lvi.SubItems.Add((Format-GuiFileSize -Bytes $f.SizeBytes)) | Out-Null
            $lvi.SubItems.Add($f.Path) | Out-Null
            $lvi.Tag = $f
            $lvFiles.Items.Add($lvi) | Out-Null
        }

        $lvFiles.EndUpdate()
        Write-LogEntry -Level 'SUCCESS' -Message "Explorador: $($dirs.Count) carpetas y $($files.Count) archivos cargados."
    }
}

function Download-SelectedFile {
    param([string]$AndroidPath)
    if ([string]::IsNullOrWhiteSpace($AndroidPath)) { return }

    $destDir = Join-Path $env:USERPROFILE 'Downloads\AndroidDownloads'
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    Write-LogEntry -Level 'INFO' -Message "Descargando '$AndroidPath' hacia '$destDir'..."
    Show-NotificationBanner -Type 'INFO' -Title "Descargando..." -Message "Transfiriendo '$AndroidPath' a tu carpeta de Descargas..."

    $worker = {
        param($src, $dst)
        $cur = Get-CurrentAdbDevice
        Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', $src, $dst)
    }

    Invoke-GuiAsyncTask -Title "Descarga ($AndroidPath)" -WorkerScript $worker -Parameters @{ src = $AndroidPath; dst = $destDir } -OnCompleted {
        param($res)
        if ($res -and $res.Success) {
            Show-NotificationBanner -Type 'SUCCESS' `
                -Title "Descarga Completada" `
                -Message "Archivo guardado en: $destDir" `
                -ActionText "Abrir Carpeta" `
                -ActionScript { Start-Process 'explorer.exe' -ArgumentList $destDir } `
                -AutoCloseSeconds 10
            Write-LogEntry -Level 'SUCCESS' -Message "Descarga completada en $destDir."
        }
    }
}

function Upload-FileToCurrentAndroidPath {
    $targetPath = $txtExpCurrentPath.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($targetPath)) { $targetPath = '/sdcard/Download' }

    $ofd = New-Object System.Windows.Forms.OpenFileDialog
    $ofd.Title = "Seleccionar archivo para subir a Android ($targetPath)"
    $ofd.Filter = "Todos los archivos (*.*)|*.*"
    if ($ofd.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
        $localFile = $ofd.FileName
        Write-LogEntry -Level 'INFO' -Message "Subiendo '$localFile' a '$targetPath'..."
        Show-NotificationBanner -Type 'INFO' -Title "Subiendo Archivo" -Message "Transfiriendo '$localFile' al telefono..."

        $worker = {
            param($src, $dst)
            $cur = Get-CurrentAdbDevice
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('push', $src, $dst)
        }

        Invoke-GuiAsyncTask -Title "Subida de Archivo" -WorkerScript $worker -Parameters @{ src = $localFile; dst = $targetPath } -OnCompleted {
            param($res)
            if ($res -and $res.Success) {
                Show-NotificationBanner -Type 'SUCCESS' -Title "Subida Exitosa" -Message "El archivo se transfirio a $targetPath."
                Refresh-AndroidExplorer
            }
        }
    }
}

function Create-AndroidFolderPrompt {
    $targetPath = $txtExpCurrentPath.Text.Trim()
    $folderName = [Microsoft.VisualBasic.Interaction]::InputBox("Escribe el nombre de la nueva carpeta:", "Nueva Carpeta en Android", "Nueva_Carpeta")
    if (-not [string]::IsNullOrWhiteSpace($folderName)) {
        $fullNew = "$targetPath/$folderName" -replace '/+', '/'
        Invoke-GuiAsyncTask -Title "Crear Carpeta" -WorkerScript {
            param($p)
            $cur = Get-CurrentAdbDevice
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell', 'mkdir', '-p', $p)
        } -Parameters @{ p = $fullNew } -OnCompleted {
            param($res)
            Refresh-AndroidExplorer
        }
    }
}

function Delete-SelectedAndroidItem {
    if ($lvFiles.SelectedItems.Count -eq 0) { return }
    $item = $lvFiles.SelectedItems[0]
    $path = [string]$item.Tag.Path
    $isDir = [bool]$item.Tag.IsDir

    $confirm = [System.Windows.Forms.MessageBox]::Show("¿Estas seguro de eliminar permanentemente:`n'$path'?", "Confirmar Eliminacion", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning)
    if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
        Invoke-GuiAsyncTask -Title "Eliminar ($path)" -WorkerScript {
            param($p, $d)
            $cur = Get-CurrentAdbDevice
            if ($d) {
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell', 'rm', '-rf', $p)
            } else {
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell', 'rm', '-f', $p)
            }
        } -Parameters @{ p = $path; d = $isDir } -OnCompleted {
            param($res)
            Show-NotificationBanner -Type 'SUCCESS' -Title "Elemento Eliminado" -Message "Se elimino $path del dispositivo."
            Refresh-AndroidExplorer
        }
    }
}

# =============================================================================
# LOGICA DE TRANSFERENCIAS RAPIDAS Y FOTOS
# =============================================================================
function Invoke-TransferTask {
    param([Parameter(Mandatory=$true)][string]$Type)

    $lblTransferDetails.Text = "Iniciando transferencia ($Type)..."
    $pbTransferProgress.Visible = $true
    $pbTransferProgress.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
    $btnOpenLocalDir.Visible = $false

    $worker = {
        param($opType)
        $cur = Get-CurrentAdbDevice
        if ($null -eq $cur -or $cur.Status -cne 'device') {
            throw "No hay un dispositivo listo conectado."
        }

        $baseDownload = Join-Path $env:USERPROFILE 'Downloads\AndroidBackup'
        $todayStr = Get-Date -Format yyyyMMdd_HHmmss

        switch ($opType) {
            'RecentPhotos' {
                $targetDir = Join-Path $baseDownload "Fotos_Recientes_$todayStr"
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                $files = @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','find','/sdcard/DCIM/Camera','-type','f','-mtime','-7','-print') | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
                if ($files.Count -eq 0) {
                    return [pscustomobject]@{ Count = 0; TargetDir = $targetDir; Msg = "No se encontraron fotos en los ultimos 7 dias." }
                }
                foreach ($f in $files) {
                    Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', ([string]$f).Trim(), $targetDir) | Out-Null
                }
                return [pscustomobject]@{ Count = $files.Count; TargetDir = $targetDir; Msg = "$($files.Count) fotos recientes transferidas con exito." }
            }
            'AllPhotos' {
                $targetDir = Join-Path $baseDownload "DCIM_Completo_$todayStr"
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', '/sdcard/DCIM/', $targetDir) | Out-Null
                return [pscustomobject]@{ Count = 1; TargetDir = $targetDir; Msg = "Carpeta DCIM completa transferida." }
            }
            'Downloads' {
                $targetDir = Join-Path $baseDownload "Downloads_$todayStr"
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', '/sdcard/Download/', $targetDir) | Out-Null
                return [pscustomobject]@{ Count = 1; TargetDir = $targetDir; Msg = "Carpeta Download transferida." }
            }
            'Documents' {
                $targetDir = Join-Path $baseDownload "Documentos_$todayStr"
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', '/sdcard/Documents/', $targetDir) | Out-Null
                return [pscustomobject]@{ Count = 1; TargetDir = $targetDir; Msg = "Documentos transferidos." }
            }
            'FullBackup' {
                $targetDir = Join-Path $baseDownload "Backup_Completo_$todayStr"
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                $folders = @('DCIM','Download','Documents','Pictures')
                foreach ($f in $folders) {
                    $sub = Join-Path $targetDir $f
                    New-Item -ItemType Directory -Path $sub -Force | Out-Null
                    Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull', "/sdcard/$f/", $sub) | Out-Null
                }
                return [pscustomobject]@{ Count = $folders.Count; TargetDir = $targetDir; Msg = "Backup completo finalizado." }
            }
        }
    }

    if ($Type -eq 'Upload') {
        Upload-FileToCurrentAndroidPath
        $pbTransferProgress.Visible = $false
        return
    }

    Invoke-GuiAsyncTask -Title "Transferencia ($Type)" -WorkerScript $worker -Parameters @{ opType = $Type } -OnCompleted {
        param($res)
        $pbTransferProgress.Visible = $false
        if ($res -and $res.Success -and $res.Output) {
            $info = $res.Output[0]
            $lblTransferDetails.Text = "✅ $($info.Msg)"
            $lblTransferDetails.ForeColor = $script:Theme.BadgeSuccessFg
            if ($info.TargetDir) {
                $btnOpenLocalDir.Tag = $info.TargetDir
                $btnOpenLocalDir.Visible = $true
                Show-NotificationBanner -Type 'SUCCESS' `
                    -Title "Transferencia Exitosa" `
                    -Message "$($info.Msg)" `
                    -ActionText "Abrir Carpeta" `
                    -ActionScript { Start-Process 'explorer.exe' -ArgumentList $info.TargetDir } `
                    -AutoCloseSeconds 12
            }
        } else {
            $lblTransferDetails.Text = "❌ Error en la transferencia: $($res.Error)"
            $lblTransferDetails.ForeColor = $script:Theme.BadgeErrorFg
        }
    }
}

function Update-DevicesTabGrid {
    param($AllDevices, $SavedDevices)
    if ($null -eq $lvDevicesTab -or $lvDevicesTab.IsDisposed) { return }

    $lvDevicesTab.BeginUpdate()
    $lvDevicesTab.Items.Clear()

    $onlineList = @($AllDevices)
    $savedList = @($SavedDevices)

    foreach ($dev in $onlineList) {
        $savedMatch = @($savedList | Where-Object { $_.UsbSerial -ceq $dev.Serial -or $_.LastEndpoint -ceq $dev.Serial })[0]
        $name = if ($savedMatch) { $savedMatch.FriendlyName } else { "Dispositivo ADB" }
        $mode = if ($dev.Serial -match ':[0-9]+$') { 'WiFi (TCP/IP)' } else { 'USB' }
        
        $lvi = New-Object System.Windows.Forms.ListViewItem("📱 $name")
        $lvi.SubItems.Add($dev.Serial) | Out-Null
        $lvi.SubItems.Add($mode) | Out-Null
        $lvi.SubItems.Add($dev.Status) | Out-Null
        $lvi.Tag = $dev
        if ($dev.Status -eq 'device') {
            $lvi.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#15803D')
        }
        $lvDevicesTab.Items.Add($lvi) | Out-Null
    }

    foreach ($s in $savedList) {
        $already = @($onlineList | Where-Object { $_.Serial -ceq $s.LastEndpoint -or $_.Serial -ceq $s.UsbSerial }).Count -gt 0
        if (-not $already) {
            $epText = if ($s.LastEndpoint) { $s.LastEndpoint } else { $s.UsbSerial }
            $lvi = New-Object System.Windows.Forms.ListViewItem("💾 $($s.FriendlyName)")
            $lvi.SubItems.Add($epText) | Out-Null
            $lvi.SubItems.Add($s.Mode) | Out-Null
            $lvi.SubItems.Add("Guardado (Offline)") | Out-Null
            $lvi.Tag = $s
            $lvi.ForeColor = $script:Theme.TextMuted
            $lvDevicesTab.Items.Add($lvi) | Out-Null
        }
    }

    $lvDevicesTab.EndUpdate()
}

# =============================================================================
# VENTANA DEDICADA COMPLETA PARA MODO AVANZADO (Consola ADB + Biblioteca)
# =============================================================================
function Show-AdvancedConsoleWindow {
    $advForm = New-Object System.Windows.Forms.Form
    $advForm.Text = "Android ADB Tools - Modo Avanzado (Consola Interactiva & Biblioteca)"
    $advForm.Size = New-Object System.Drawing.Size(1280, 800)
    $advForm.MinimumSize = New-Object System.Drawing.Size(1020, 640)
    $advForm.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $advForm.WindowState = [System.Windows.Forms.FormWindowState]::Maximized
    $advForm.BackColor = $script:Theme.BgWindow
    $advForm.Font = $script:Fonts.Body

    # Top Header en Modo Avanzado
    $advHeader = New-Object System.Windows.Forms.Panel
    $advHeader.Dock = [System.Windows.Forms.DockStyle]::Top
    $advHeader.Height = 56
    $advHeader.BackColor = $script:Theme.BgHeader
    $advHeader.Padding = New-Object System.Windows.Forms.Padding(16, 10, 16, 10)

    # 3. BOTON PARA REGRESAR A MODO NORMAL (Solicitud #3)
    $btnBackToNormal = New-Object System.Windows.Forms.Button
    $btnBackToNormal.Text = "⬅️ Volver a Modo Normal"
    $btnBackToNormal.Font = $script:Fonts.BodyBold
    $btnBackToNormal.BackColor = $script:Theme.Primary
    $btnBackToNormal.ForeColor = $script:Theme.PrimaryFg
    $btnBackToNormal.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnBackToNormal.FlatAppearance.BorderSize = 0
    $btnBackToNormal.Location = New-Object System.Drawing.Point(16, 12)
    $btnBackToNormal.Size = New-Object System.Drawing.Size(190, 32)
    $btnBackToNormal.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnBackToNormal.Add_Click({ $advForm.Close() })
    $advHeader.Controls.Add($btnBackToNormal)

    $advTitle = New-Object System.Windows.Forms.Label
    $advTitle.Text = "⚡ CONSOLA AVANZADA ADB & BIBLIOTECA DE COMANDOS"
    $advTitle.Font = $script:Fonts.TitleMid
    $advTitle.ForeColor = $script:Theme.TextTitle
    $advTitle.Location = New-Object System.Drawing.Point(220, 16)
    $advTitle.Size = New-Object System.Drawing.Size(550, 24)
    $advHeader.Controls.Add($advTitle)

    $curDev = Get-CurrentAdbDevice
    $advTarget = New-Object System.Windows.Forms.Label
    $advTarget.Text = if ($curDev) { "📱 Destino: $($curDev.Serial)" } else { "📱 Destino: General" }
    $advTarget.Font = $script:Fonts.SmallBold
    $advTarget.ForeColor = $script:Theme.Primary
    $advTarget.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right
    $advTarget.Location = New-Object System.Drawing.Point(900, 18)
    $advTarget.Size = New-Object System.Drawing.Size(350, 20)
    $advTarget.TextAlign = [System.Drawing.ContentAlignment]::MiddleRight
    $advHeader.Controls.Add($advTarget)

    $advHeaderBorder = New-Object System.Windows.Forms.Panel
    $advHeaderBorder.Dock = [System.Windows.Forms.DockStyle]::Bottom
    $advHeaderBorder.Height = 1
    $advHeaderBorder.BackColor = $script:Theme.BorderCard
    $advHeader.Controls.Add($advHeaderBorder)

    $advForm.Controls.Add($advHeader)

    # Contenedor Dividido (Split Layout 50/50)
    $advSplitContainer = New-Object System.Windows.Forms.SplitContainer
    $advSplitContainer.Dock = [System.Windows.Forms.DockStyle]::Fill
    $advSplitContainer.Orientation = [System.Windows.Forms.Orientation]::Vertical
    $advSplitContainer.SplitterDistance = 620
    $advSplitContainer.SplitterWidth = 8
    $advSplitContainer.BackColor = $script:Theme.BgWindow

    # -------------------------------------------------------------------------
    # PANEL IZQUIERDO: CONSOLA INTERACTIVA ADB (CMD PROPIO)
    # -------------------------------------------------------------------------
    $pnlLeftConsole = New-Object System.Windows.Forms.Panel
    $pnlLeftConsole.Dock = [System.Windows.Forms.DockStyle]::Fill
    $pnlLeftConsole.BackColor = $script:Theme.BgCard
    $pnlLeftConsole.Padding = New-Object System.Windows.Forms.Padding(14)

    $lblConsoleHead = New-Object System.Windows.Forms.Label
    $lblConsoleHead.Text = "💻 Terminal Interactiva ADB (Ingreso de Comandos Propios)"
    $lblConsoleHead.Font = $script:Fonts.TitleSmall
    $lblConsoleHead.ForeColor = $script:Theme.TextTitle
    $lblConsoleHead.Dock = [System.Windows.Forms.DockStyle]::Top
    $lblConsoleHead.Height = 26
    $pnlLeftConsole.Controls.Add($lblConsoleHead)

    # Fila de Favoritos
    $pnlAdvFavRow = New-Object System.Windows.Forms.Panel
    $pnlAdvFavRow.Dock = [System.Windows.Forms.DockStyle]::Top
    $pnlAdvFavRow.Height = 36

    $lblFavAdv = New-Object System.Windows.Forms.Label
    $lblFavAdv.Text = "Favoritos:"
    $lblFavAdv.Font = $script:Fonts.SmallBold
    $lblFavAdv.Location = New-Object System.Drawing.Point(0, 8)
    $lblFavAdv.Size = New-Object System.Drawing.Size(65, 20)
    $pnlAdvFavRow.Controls.Add($lblFavAdv)

    $cboAdvFavs = New-Object System.Windows.Forms.ComboBox
    $cboAdvFavs.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
    $cboAdvFavs.Font = $script:Fonts.Small
    $cboAdvFavs.Location = New-Object System.Drawing.Point(68, 5)
    $cboAdvFavs.Size = New-Object System.Drawing.Size(370, 24)
    $pnlAdvFavRow.Controls.Add($cboAdvFavs)

    $btnAdvLoadFav = New-Object System.Windows.Forms.Button
    $btnAdvLoadFav.Text = "Usar"
    $btnAdvLoadFav.Font = $script:Fonts.SmallBold
    $btnAdvLoadFav.Location = New-Object System.Drawing.Point(445, 4)
    $btnAdvLoadFav.Size = New-Object System.Drawing.Size(65, 26)
    $btnAdvLoadFav.BackColor = $script:Theme.Secondary
    $btnAdvLoadFav.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvLoadFav.Cursor = [System.Windows.Forms.Cursors]::Hand
    $pnlAdvFavRow.Controls.Add($btnAdvLoadFav)

    $btnAdvDelFav = New-Object System.Windows.Forms.Button
    $btnAdvDelFav.Text = "🗑️"
    $btnAdvDelFav.Font = $script:Fonts.Small
    $btnAdvDelFav.Location = New-Object System.Drawing.Point(515, 4)
    $btnAdvDelFav.Size = New-Object System.Drawing.Size(40, 26)
    $btnAdvDelFav.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#FEE2E2')
    $btnAdvDelFav.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#DC2626')
    $btnAdvDelFav.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvDelFav.Cursor = [System.Windows.Forms.Cursors]::Hand
    $pnlAdvFavRow.Controls.Add($btnAdvDelFav)

    $pnlLeftConsole.Controls.Add($pnlAdvFavRow)

    # Fila de Entrada de Comandos (CMD PROPIO - Solicitud #2)
    $pnlAdvInputRow = New-Object System.Windows.Forms.Panel
    $pnlAdvInputRow.Dock = [System.Windows.Forms.DockStyle]::Top
    $pnlAdvInputRow.Height = 44

    $lblAdvPrompt = New-Object System.Windows.Forms.Label
    $lblAdvPrompt.Text = "adb>"
    $lblAdvPrompt.Font = $script:Fonts.CodeBold
    $lblAdvPrompt.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
    $lblAdvPrompt.Location = New-Object System.Drawing.Point(0, 10)
    $lblAdvPrompt.Size = New-Object System.Drawing.Size(42, 24)
    $pnlAdvInputRow.Controls.Add($lblAdvPrompt)

    $txtAdvCommandInput = New-Object System.Windows.Forms.TextBox
    $txtAdvCommandInput.Font = $script:Fonts.CodeLarge
    $txtAdvCommandInput.Text = "shell getprop ro.product.model"
    $txtAdvCommandInput.Location = New-Object System.Drawing.Point(46, 7)
    $txtAdvCommandInput.Size = New-Object System.Drawing.Size(420, 28)
    $pnlAdvInputRow.Controls.Add($txtAdvCommandInput)

    $btnAdvExecute = New-Object System.Windows.Forms.Button
    $btnAdvExecute.Text = "▶ Ejecutar"
    $btnAdvExecute.Font = $script:Fonts.BodyBold
    $btnAdvExecute.BackColor = $script:Theme.Primary
    $btnAdvExecute.ForeColor = $script:Theme.PrimaryFg
    $btnAdvExecute.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvExecute.FlatAppearance.BorderSize = 0
    $btnAdvExecute.Location = New-Object System.Drawing.Point(472, 5)
    $btnAdvExecute.Size = New-Object System.Drawing.Size(110, 32)
    $btnAdvExecute.Cursor = [System.Windows.Forms.Cursors]::Hand
    $pnlAdvInputRow.Controls.Add($btnAdvExecute)

    $pnlLeftConsole.Controls.Add($pnlAdvInputRow)

    # Opciones de Consola
    $pnlAdvOptionsRow = New-Object System.Windows.Forms.Panel
    $pnlAdvOptionsRow.Dock = [System.Windows.Forms.DockStyle]::Top
    $pnlAdvOptionsRow.Height = 32

    $chkAdvSaveFav = New-Object System.Windows.Forms.CheckBox
    $chkAdvSaveFav.Text = "⭐ Guardar como favorito"
    $chkAdvSaveFav.Font = $script:Fonts.Small
    $chkAdvSaveFav.Location = New-Object System.Drawing.Point(46, 4)
    $chkAdvSaveFav.Size = New-Object System.Drawing.Size(180, 22)
    $pnlAdvOptionsRow.Controls.Add($chkAdvSaveFav)

    $btnAdvPaste = New-Object System.Windows.Forms.Button
    $btnAdvPaste.Text = "📋 Pegar"
    $btnAdvPaste.Font = $script:Fonts.Small
    $btnAdvPaste.Location = New-Object System.Drawing.Point(235, 3)
    $btnAdvPaste.Size = New-Object System.Drawing.Size(70, 24)
    $btnAdvPaste.BackColor = $script:Theme.Secondary
    $btnAdvPaste.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvPaste.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnAdvPaste.Add_Click({
        if ([System.Windows.Forms.Clipboard]::ContainsText()) {
            $txtAdvCommandInput.Text = [System.Windows.Forms.Clipboard]::GetText().Trim()
        }
    })
    $pnlAdvOptionsRow.Controls.Add($btnAdvPaste)

    $btnAdvClear = New-Object System.Windows.Forms.Button
    $btnAdvClear.Text = "🧹 Limpiar"
    $btnAdvClear.Font = $script:Fonts.Small
    $btnAdvClear.Location = New-Object System.Drawing.Point(310, 3)
    $btnAdvClear.Size = New-Object System.Drawing.Size(75, 24)
    $btnAdvClear.BackColor = $script:Theme.Secondary
    $btnAdvClear.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvClear.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnAdvClear.Add_Click({ $txtAdvCommandInput.Clear(); $txtAdvCommandInput.Focus() })
    $pnlAdvOptionsRow.Controls.Add($btnAdvClear)

    $pnlLeftConsole.Controls.Add($pnlAdvOptionsRow)

    # Terminal de Salida Grande
    $script:txtAdvConsoleOutput = New-Object System.Windows.Forms.RichTextBox
    $script:txtAdvConsoleOutput.Dock = [System.Windows.Forms.DockStyle]::Fill
    $script:txtAdvConsoleOutput.BackColor = $script:Theme.LogBg
    $script:txtAdvConsoleOutput.ForeColor = $script:Theme.LogFg
    $script:txtAdvConsoleOutput.Font = $script:Fonts.CodeLarge
    $script:txtAdvConsoleOutput.ReadOnly = $true
    $script:txtAdvConsoleOutput.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $pnlLeftConsole.Controls.Add($script:txtAdvConsoleOutput)

    $advSplitContainer.Panel1.Controls.Add($pnlLeftConsole)

    # -------------------------------------------------------------------------
    # PANEL DERECHO: BIBLIOTECA DE COMANDOS POR SECCIONES (Solicitud #1)
    # -------------------------------------------------------------------------
    $pnlRightCatalog = New-Object System.Windows.Forms.Panel
    $pnlRightCatalog.Dock = [System.Windows.Forms.DockStyle]::Fill
    $pnlRightCatalog.BackColor = $script:Theme.BgCard
    $pnlRightCatalog.Padding = New-Object System.Windows.Forms.Padding(12)

    $lblCatHead = New-Object System.Windows.Forms.Label
    $lblCatHead.Text = "📚 BIBLIOTECA DE COMANDOS ORGANIZADA"
    $lblCatHead.Font = $script:Fonts.TitleSmall
    $lblCatHead.ForeColor = $script:Theme.TextTitle
    $lblCatHead.Dock = [System.Windows.Forms.DockStyle]::Top
    $lblCatHead.Height = 26
    $pnlRightCatalog.Controls.Add($lblCatHead)

    # Fila de Buscador y Acciones Directas
    $pnlCatTopBar = New-Object System.Windows.Forms.Panel
    $pnlCatTopBar.Dock = [System.Windows.Forms.DockStyle]::Top
    $pnlCatTopBar.Height = 38
    $pnlCatTopBar.Padding = New-Object System.Windows.Forms.Padding(0, 4, 0, 4)

    $lblSearchIcon = New-Object System.Windows.Forms.Label
    $lblSearchIcon.Text = "🔍"
    $lblSearchIcon.Font = $script:Fonts.Body
    $lblSearchIcon.Location = New-Object System.Drawing.Point(0, 8)
    $lblSearchIcon.Size = New-Object System.Drawing.Size(24, 22)
    $pnlCatTopBar.Controls.Add($lblSearchIcon)

    $txtCatSearch = New-Object System.Windows.Forms.TextBox
    $txtCatSearch.Font = $script:Fonts.Body
    $txtCatSearch.Location = New-Object System.Drawing.Point(26, 5)
    $txtCatSearch.Size = New-Object System.Drawing.Size(220, 26)
    $pnlCatTopBar.Controls.Add($txtCatSearch)

    $btnAdvRunDirect = New-Object System.Windows.Forms.Button
    $btnAdvRunDirect.Text = "▶ Ejecutar"
    $btnAdvRunDirect.Font = $script:Fonts.SmallBold
    $btnAdvRunDirect.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
    $btnAdvRunDirect.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#FFFFFF')
    $btnAdvRunDirect.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvRunDirect.FlatAppearance.BorderSize = 0
    $btnAdvRunDirect.Location = New-Object System.Drawing.Point(252, 4)
    $btnAdvRunDirect.Size = New-Object System.Drawing.Size(90, 28)
    $btnAdvRunDirect.Cursor = [System.Windows.Forms.Cursors]::Hand
    $pnlCatTopBar.Controls.Add($btnAdvRunDirect)

    $btnAdvLoadToConsole = New-Object System.Windows.Forms.Button
    $btnAdvLoadToConsole.Text = "✏️ Cargar en CMD"
    $btnAdvLoadToConsole.Font = $script:Fonts.SmallBold
    $btnAdvLoadToConsole.BackColor = $script:Theme.Secondary
    $btnAdvLoadToConsole.ForeColor = $script:Theme.SecondaryFg
    $btnAdvLoadToConsole.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvLoadToConsole.FlatAppearance.BorderSize = 1
    $btnAdvLoadToConsole.FlatAppearance.BorderColor = $script:Theme.BorderCard
    $btnAdvLoadToConsole.Location = New-Object System.Drawing.Point(347, 4)
    $btnAdvLoadToConsole.Size = New-Object System.Drawing.Size(125, 28)
    $btnAdvLoadToConsole.Cursor = [System.Windows.Forms.Cursors]::Hand
    $pnlCatTopBar.Controls.Add($btnAdvLoadToConsole)

    $btnAdvCopyCmd = New-Object System.Windows.Forms.Button
    $btnAdvCopyCmd.Text = "📋 Copiar"
    $btnAdvCopyCmd.Font = $script:Fonts.SmallBold
    $btnAdvCopyCmd.BackColor = $script:Theme.Secondary
    $btnAdvCopyCmd.ForeColor = $script:Theme.SecondaryFg
    $btnAdvCopyCmd.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnAdvCopyCmd.FlatAppearance.BorderSize = 1
    $btnAdvCopyCmd.FlatAppearance.BorderColor = $script:Theme.BorderCard
    $btnAdvCopyCmd.Location = New-Object System.Drawing.Point(477, 4)
    $btnAdvCopyCmd.Size = New-Object System.Drawing.Size(80, 28)
    $btnAdvCopyCmd.Cursor = [System.Windows.Forms.Cursors]::Hand
    $pnlCatTopBar.Controls.Add($btnAdvCopyCmd)

    $pnlRightCatalog.Controls.Add($pnlCatTopBar)

    # Pie con sugerencia (Dock Bottom)
    $lblCatHint = New-Object System.Windows.Forms.Label
    $lblCatHint.Text = "💡 Doble clic en cualquier comando para ejecutarlo o presiona Enter en la barra adb>."
    $lblCatHint.Font = $script:Fonts.Small
    $lblCatHint.ForeColor = $script:Theme.TextMuted
    $lblCatHint.Dock = [System.Windows.Forms.DockStyle]::Bottom
    $lblCatHint.Height = 22
    $pnlRightCatalog.Controls.Add($lblCatHint)

    # TabControl de Secciones por Categoria (Dock Fill)
    $tabControlSections = New-Object System.Windows.Forms.TabControl
    $tabControlSections.Dock = [System.Windows.Forms.DockStyle]::Fill
    $tabControlSections.Font = $script:Fonts.SmallBold
    $tabControlSections.Padding = New-Object System.Drawing.Point(8, 5)

    # Mapa de Secciones: Categoria -> ListView
    $script:SectionListViewMap = @{}

    $categoriesList = @(
        [pscustomobject]@{ Title = '🌟 Todos'; CatName = '🌟 Todos los comandos' },
        [pscustomobject]@{ Title = '📊 Diagnostico'; CatName = 'Diagnostico & Rendimiento' },
        [pscustomobject]@{ Title = '🔋 Bateria'; CatName = 'Bateria & Sensores' },
        [pscustomobject]@{ Title = '📱 Sistema'; CatName = 'Sistema & Pantalla' },
        [pscustomobject]@{ Title = '📦 Apps'; CatName = 'Apps & Paquetes' },
        [pscustomobject]@{ Title = '🌐 Red'; CatName = 'Red & Conectividad' },
        [pscustomobject]@{ Title = '🎮 Teclas'; CatName = 'Control & Teclas' },
        [pscustomobject]@{ Title = '📜 Logcat'; CatName = 'Logcat & Logs' },
        [pscustomobject]@{ Title = '🔄 Reinicio'; CatName = 'Reinicio & Modos' }
    )

    foreach ($sec in $categoriesList) {
        $tp = New-Object System.Windows.Forms.TabPage
        $tp.Text = " $($sec.Title) "
        $tp.BackColor = $script:Theme.BgCard
        $tp.Padding = New-Object System.Windows.Forms.Padding(4)

        $lv = New-Object System.Windows.Forms.ListView
        $lv.Dock = [System.Windows.Forms.DockStyle]::Fill
        $lv.View = [System.Windows.Forms.View]::Details
        $lv.FullRowSelect = $true
        $lv.GridLines = $true
        $lv.HeaderStyle = [System.Windows.Forms.ColumnHeaderStyle]::Nonclickable
        $lv.Font = $script:Fonts.Body
        $lv.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
        $lv.BackColor = $script:Theme.BgWindow
        $lv.Cursor = [System.Windows.Forms.Cursors]::Hand

        $lv.Columns.Add("Nombre / Acción", 195) | Out-Null
        $lv.Columns.Add("Sintaxis ADB", 200) | Out-Null
        $lv.Columns.Add("Descripción", 230) | Out-Null
        
        $tp.Controls.Add($lv)
        $tabControlSections.TabPages.Add($tp)
        $script:SectionListViewMap[$sec.CatName] = @{ Page = $tp; ListView = $lv; Title = $sec.Title }
    }

    $pnlRightCatalog.Controls.Add($tabControlSections)
    # Z-Order de WinForms: asegurar que TabControl llene y el Hint quede abajo
    $tabControlSections.BringToFront()

    $advSplitContainer.Panel2.Controls.Add($pnlRightCatalog)
    $advForm.Controls.Add($advSplitContainer)

    # -------------------------------------------------------------------------
    # LOGICA DE EVENTOS DE MODO AVANZADO
    # -------------------------------------------------------------------------
    $script:AdvCommandHistory = New-Object System.Collections.Generic.List[string]
    $script:AdvCommandHistoryIndex = -1

    function Reload-AdvFavs {
        $cboAdvFavs.Items.Clear()
        $favs = Get-AdbFavoriteCommands
        foreach ($f in $favs) {
            $cboAdvFavs.Items.Add("$($f.Name) -> $($f.Command)") | Out-Null
        }
        if ($cboAdvFavs.Items.Count -gt 0) { $cboAdvFavs.SelectedIndex = 0 }
    }

    function Update-AdvCatalogFilter {
        $searchTxt = $txtCatSearch.Text.Trim().ToLower()

        foreach ($catKey in $script:SectionListViewMap.Keys) {
            $info = $script:SectionListViewMap[$catKey]
            $lv = $info.ListView
            
            $lv.BeginUpdate()
            $lv.Items.Clear()

            foreach ($cmd in $script:AdvancedCommandExamples) {
                $matchCat = ($catKey -eq '🌟 Todos los comandos' -or $cmd.Category -eq $catKey)
                $matchText = ([string]::IsNullOrWhiteSpace($searchTxt) -or 
                              $cmd.Name.ToLower().Contains($searchTxt) -or 
                              $cmd.Command.ToLower().Contains($searchTxt) -or 
                              $cmd.Description.ToLower().Contains($searchTxt))

                if ($matchCat -and $matchText) {
                    $lvi = New-Object System.Windows.Forms.ListViewItem($cmd.Name)
                    $lvi.SubItems.Add("adb $($cmd.Command)") | Out-Null
                    $lvi.SubItems.Add($cmd.Description) | Out-Null
                    $lvi.Tag = $cmd
                    $lv.Items.Add($lvi) | Out-Null
                }
            }

            if ($lv.Items.Count -gt 0) { $lv.Items[0].Selected = $true }
            $lv.EndUpdate()
        }
    }

    function Get-SelectedAdvCommand {
        $activeTab = $tabControlSections.SelectedTab
        if ($null -eq $activeTab) { return $null }

        foreach ($catKey in $script:SectionListViewMap.Keys) {
            $info = $script:SectionListViewMap[$catKey]
            if ($info.Page -eq $activeTab) {
                $lv = $info.ListView
                if ($lv.SelectedItems.Count -gt 0) {
                    return $lv.SelectedItems[0].Tag
                }
            }
        }
        return $null
    }

    # Conectar doble clic en cada ListView de seccion
    foreach ($catKey in $script:SectionListViewMap.Keys) {
        $lv = $script:SectionListViewMap[$catKey].ListView
        $lv.Add_DoubleClick({
            $cmdObj = Get-SelectedAdvCommand
            if ($null -ne $cmdObj) {
                $txtAdvCommandInput.Text = $cmdObj.Command
                $btnAdvExecute.PerformClick()
            }
        })
    }

    $txtCatSearch.Add_TextChanged({ Update-AdvCatalogFilter })

    $btnAdvLoadFav.Add_Click({
        if ($cboAdvFavs.SelectedIndex -ge 0) {
            $favs = Get-AdbFavoriteCommands
            $txtAdvCommandInput.Text = $favs[$cboAdvFavs.SelectedIndex].Command
        }
    })

    $btnAdvDelFav.Add_Click({
        if ($cboAdvFavs.SelectedIndex -ge 0) {
            $favs = @(Get-AdbFavoriteCommands)
            $target = $favs[$cboAdvFavs.SelectedIndex]
            $updated = @($favs | Where-Object { $_ -ne $target })
            Save-AdbFavoriteCommands -Commands $updated
            Reload-AdvFavs
        }
    })

    $btnAdvLoadToConsole.Add_Click({
        $cmdObj = Get-SelectedAdvCommand
        if ($null -ne $cmdObj) {
            $txtAdvCommandInput.Text = $cmdObj.Command
            $txtAdvCommandInput.Focus()
        }
    })

    $btnAdvRunDirect.Add_Click({
        $cmdObj = Get-SelectedAdvCommand
        if ($null -ne $cmdObj) {
            $txtAdvCommandInput.Text = $cmdObj.Command
            $btnAdvExecute.PerformClick()
        }
    })

    $btnAdvCopyCmd.Add_Click({
        $cmdObj = Get-SelectedAdvCommand
        if ($null -ne $cmdObj) {
            [System.Windows.Forms.Clipboard]::SetText("adb $($cmdObj.Command)")
        }
    })

    # EJECUCION DE COMANDOS PROPIOS (Solicitud #2)
    $btnAdvExecute.Add_Click({
        $rawCmd = $txtAdvCommandInput.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($rawCmd)) { return }

        # Historial de comandos
        if ($script:AdvCommandHistory.Count -eq 0 -or $script:AdvCommandHistory[$script:AdvCommandHistory.Count - 1] -ne $rawCmd) {
            $script:AdvCommandHistory.Add($rawCmd) | Out-Null
        }
        $script:AdvCommandHistoryIndex = $script:AdvCommandHistory.Count

        # Guardar favorito si checkbox esta activo
        if ($chkAdvSaveFav.Checked) {
            $favs = @(Get-AdbFavoriteCommands)
            $favs += [pscustomobject]@{ Name = $rawCmd; Command = $rawCmd }
            Save-AdbFavoriteCommands -Commands $favs
            Reload-AdvFavs
            $chkAdvSaveFav.Checked = $false
        }

        # Imprimir comando en consola con formato verde/emerald
        $timeStr = [DateTime]::Now.ToString("HH:mm:ss")
        $curDev = Get-CurrentAdbDevice

        $script:txtAdvConsoleOutput.SelectionStart = $script:txtAdvConsoleOutput.TextLength
        $script:txtAdvConsoleOutput.SelectionColor = [System.Drawing.ColorTranslator]::FromHtml('#10B981')
        $script:txtAdvConsoleOutput.SelectionFont = $script:Fonts.CodeBold
        $script:txtAdvConsoleOutput.AppendText("`n[$timeStr] adb> $rawCmd`n")
        $script:txtAdvConsoleOutput.SelectionColor = $script:Theme.LogFg
        $script:txtAdvConsoleOutput.SelectionFont = $script:Fonts.CodeLarge

        # Normalizar comando (si empieza con "adb ")
        $cleanCmd = $rawCmd
        if ($cleanCmd.ToLower().StartsWith("adb ")) {
            $cleanCmd = $cleanCmd.Substring(4).Trim()
        }
        $cmdParts = $cleanCmd -split '\s+' | Where-Object { $_ -ne '' }

        # Ejecutar ADB en el dispositivo actual o general
        try {
            $outLines = @()
            if ($curDev -and $curDev.Serial) {
                $outLines = @(Invoke-AdbCommand -Serial $curDev.Serial -Arguments $cmdParts 2>&1)
            } else {
                $outLines = @(& adb @cmdParts 2>&1)
            }

            if ($outLines.Count -gt 0) {
                $txtOut = ($outLines | ForEach-Object { [string]$_ }) -join "`n"
                $script:txtAdvConsoleOutput.AppendText("$txtOut`n")
            } else {
                $script:txtAdvConsoleOutput.SelectionColor = [System.Drawing.ColorTranslator]::FromHtml('#9CA3AF')
                $script:txtAdvConsoleOutput.AppendText("(Comando ejecutado sin salida en pantalla)`n")
            }
        } catch {
            $script:txtAdvConsoleOutput.SelectionColor = [System.Drawing.ColorTranslator]::FromHtml('#EF4444')
            $script:txtAdvConsoleOutput.AppendText("ERROR: $($_.Exception.Message)`n")
        }

        $script:txtAdvConsoleOutput.SelectionStart = $script:txtAdvConsoleOutput.TextLength
        $script:txtAdvConsoleOutput.ScrollToCaret()
        $txtAdvCommandInput.SelectAll()
        $txtAdvCommandInput.Focus()
    })

    # Tecla Enter e Historial Arriba/Abajo para la entrada de comandos CMD
    $txtAdvCommandInput.Add_KeyDown({
        param($sender, $e)
        if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Enter) {
            $e.SuppressKeyPress = $true
            $btnAdvExecute.PerformClick()
        }
        elseif ($e.KeyCode -eq [System.Windows.Forms.Keys]::Up) {
            if ($script:AdvCommandHistory.Count -gt 0) {
                if ($script:AdvCommandHistoryIndex -gt 0) {
                    $script:AdvCommandHistoryIndex--
                }
                $txtAdvCommandInput.Text = $script:AdvCommandHistory[$script:AdvCommandHistoryIndex]
                $txtAdvCommandInput.SelectionStart = $txtAdvCommandInput.Text.Length
            }
        }
        elseif ($e.KeyCode -eq [System.Windows.Forms.Keys]::Down) {
            if ($script:AdvCommandHistory.Count -gt 0) {
                if ($script:AdvCommandHistoryIndex -lt ($script:AdvCommandHistory.Count - 1)) {
                    $script:AdvCommandHistoryIndex++
                    $txtAdvCommandInput.Text = $script:AdvCommandHistory[$script:AdvCommandHistoryIndex]
                } else {
                    $script:AdvCommandHistoryIndex = $script:AdvCommandHistory.Count
                    $txtAdvCommandInput.Clear()
                }
                $txtAdvCommandInput.SelectionStart = $txtAdvCommandInput.Text.Length
            }
        }
    })

    $advForm.Add_Shown({
        Reload-AdvFavs
        Update-AdvCatalogFilter
        $txtAdvCommandInput.Focus()
    })

    $null = $advForm.ShowDialog($form)
}

# =============================================================================
# DIALOGOS DE GESTION (Apps, Pantalla, Asistente)
# =============================================================================
function Show-NewDeviceWizardDialog {
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Conectar Nuevo Dispositivo Android"
    $dlg.Size = New-Object System.Drawing.Size(460, 240)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dlg.MaximizeBox = $false
    $dlg.MinimizeBox = $false
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.Font = $script:Fonts.Body

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "Introduce la direccion IP y Puerto WiFi de tu telefono:"
    $lbl.Location = New-Object System.Drawing.Point(20, 20)
    $lbl.Size = New-Object System.Drawing.Size(400, 20)
    $dlg.Controls.Add($lbl)

    $txtIp = New-Object System.Windows.Forms.TextBox
    $txtIp.Text = "192.168.1.50:5555"
    $txtIp.Font = $script:Fonts.CodeBold
    $txtIp.Location = New-Object System.Drawing.Point(20, 50)
    $txtIp.Size = New-Object System.Drawing.Size(400, 28)
    $dlg.Controls.Add($txtIp)

    $btnConn = New-Object System.Windows.Forms.Button
    $btnConn.Text = "Conectar"
    $btnConn.Font = $script:Fonts.BodyBold
    $btnConn.BackColor = $script:Theme.Primary
    $btnConn.ForeColor = $script:Theme.PrimaryFg
    $btnConn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnConn.Location = New-Object System.Drawing.Point(290, 140)
    $btnConn.Size = New-Object System.Drawing.Size(130, 36)
    $btnConn.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnConn.Add_Click({
        $ep = $txtIp.Text.Trim()
        $dlg.Close()
        if (-not [string]::IsNullOrWhiteSpace($ep)) {
            Invoke-GuiAsyncTask -Title "Conectar a $ep" -WorkerScript {
                param($endpoint)
                Invoke-AdbCommand -Arguments @('connect', $endpoint)
            } -Parameters @{ endpoint = $ep } -OnCompleted {
                param($res)
                Refresh-DeviceStatus
            }
        }
    })
    $dlg.Controls.Add($btnConn)
    $null = $dlg.ShowDialog($form)
}

function Show-AppsManagerDialog {
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Gestion de Aplicaciones Android"
    $dlg.Size = New-Object System.Drawing.Size(560, 480)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.Font = $script:Fonts.Body

    $btnInstall = New-Object System.Windows.Forms.Button
    $btnInstall.Text = "📦 Instalar APK desde PC"
    $btnInstall.Font = $script:Fonts.BodyBold
    $btnInstall.BackColor = $script:Theme.Primary
    $btnInstall.ForeColor = $script:Theme.PrimaryFg
    $btnInstall.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnInstall.Size = New-Object System.Drawing.Size(500, 40)
    $btnInstall.Location = New-Object System.Drawing.Point(20, 20)
    $btnInstall.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnInstall.Add_Click({
        $ofd = New-Object System.Windows.Forms.OpenFileDialog
        $ofd.Filter = "Paquete APK Android (*.apk)|*.apk"
        if ($ofd.ShowDialog($dlg) -eq [System.Windows.Forms.DialogResult]::OK) {
            $apk = $ofd.FileName
            Invoke-GuiAsyncTask -Title "Instalar APK" -WorkerScript {
                param($p)
                $cur = Get-CurrentAdbDevice
                Invoke-AdbCommand -Serial $cur.Serial -Arguments @('install', '-r', $p)
            } -Parameters @{ p = $apk }
        }
    })
    $dlg.Controls.Add($btnInstall)

    $lstApps = New-Object System.Windows.Forms.ListBox
    $lstApps.Location = New-Object System.Drawing.Point(20, 75)
    $lstApps.Size = New-Object System.Drawing.Size(500, 320)
    $lstApps.Font = $script:Fonts.Small
    $dlg.Controls.Add($lstApps)

    $dlg.Add_Shown({
        Invoke-GuiAsyncTask -Title "Listar Apps" -WorkerScript {
            $cur = Get-CurrentAdbDevice
            @(Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','pm','list','packages','-3'))
        } -OnCompleted {
            param($res)
            if ($res -and $res.Output) {
                $lstApps.Items.Clear()
                foreach ($p in $res.Output) {
                    $clean = $p -replace '^package:', ''
                    if (-not [string]::IsNullOrWhiteSpace($clean)) { $lstApps.Items.Add($clean) | Out-Null }
                }
            }
        }
    })

    $null = $dlg.ShowDialog($form)
}

function Show-ScreenDialog {
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = "Captura y Espejo de Pantalla"
    $dlg.Size = New-Object System.Drawing.Size(460, 220)
    $dlg.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $dlg.BackColor = $script:Theme.BgWindow
    $dlg.Font = $script:Fonts.Body

    $btnCap = New-Object System.Windows.Forms.Button
    $btnCap.Text = "📸 Tomar Captura de Pantalla"
    $btnCap.Font = $script:Fonts.BodyBold
    $btnCap.Size = New-Object System.Drawing.Size(400, 44)
    $btnCap.Location = New-Object System.Drawing.Point(24, 25)
    $btnCap.BackColor = $script:Theme.Primary
    $btnCap.ForeColor = $script:Theme.PrimaryFg
    $btnCap.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnCap.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnCap.Add_Click({
        $dlg.Close()
        $dest = Join-Path $env:USERPROFILE ("Downloads\screenshot_{0}.png" -f (Get-Date -Format yyyyMMdd_HHmmss))
        Invoke-GuiAsyncTask -Title "Captura de Pantalla" -WorkerScript {
            param($out)
            $cur = Get-CurrentAdbDevice
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','screencap','-p','/sdcard/screen.png') | Out-Null
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('pull','/sdcard/screen.png', $out) | Out-Null
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','rm','/sdcard/screen.png') | Out-Null
        } -Parameters @{ out = $dest } -OnCompleted {
            param($res)
            Show-NotificationBanner -Type 'SUCCESS' -Title "Captura Guardada" -Message "Guardada en: $dest" -ActionText "Ver Imagen" -ActionScript { Start-Process $dest }
        }
    })
    $dlg.Controls.Add($btnCap)

    $btnScrcpy = New-Object System.Windows.Forms.Button
    $btnScrcpy.Text = "🖥️ Iniciar Espejo de Pantalla (scrcpy)"
    $btnScrcpy.Font = $script:Fonts.BodyBold
    $btnScrcpy.Size = New-Object System.Drawing.Size(400, 44)
    $btnScrcpy.Location = New-Object System.Drawing.Point(24, 85)
    $btnScrcpy.BackColor = $script:Theme.Secondary
    $btnScrcpy.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btnScrcpy.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btnScrcpy.Add_Click({
        $dlg.Close()
        try {
            if (Test-ScrcpyAvailable) {
                Start-AdbScrcpy | Out-Null
            }
        } catch {
            Show-NotificationBanner -Type 'WARN' -Title "scrcpy no disponible" -Message "Instala scrcpy para habilitar el control y espejo remoto de pantalla."
        }
    })
    $dlg.Controls.Add($btnScrcpy)
    $null = $dlg.ShowDialog($form)
}

function Invoke-DeviceCleanCaches {
    $confirm = [System.Windows.Forms.MessageBox]::Show("¿Deseas ejecutar la optimizacion y limpieza de caches de aplicaciones en el dispositivo?", "Confirmar Limpieza", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($confirm -eq [System.Windows.Forms.DialogResult]::Yes) {
        Write-LogEntry -Level 'INFO' -Message "Ejecutando liberacion de caches temporales (pm trim-caches)..."
        Invoke-GuiAsyncTask -Title "Limpieza de Cache" -WorkerScript {
            $cur = Get-CurrentAdbDevice
            Invoke-AdbCommand -Serial $cur.Serial -Arguments @('shell','pm','trim-caches','1000M') | Out-Null
        } -OnCompleted {
            param($res)
            Show-NotificationBanner -Type 'SUCCESS' -Title "Limpieza Finalizada" -Message "Se liberaron caches temporales del sistema Android."
        }
    }
}

function Invoke-FullDiagnostics {
    $tabControlMain.SelectedTab = $tabLogs
    Write-LogEntry -Level 'INFO' -Message "Iniciando analisis de diagnostico del entorno ADB..."
    Invoke-GuiAsyncTask -Title "Diagnostico Completo" -WorkerScript {
        Invoke-AdbDiagnostics -NoPause
    } -OnCompleted {
        param($res)
        if ($res -and $res.Output) {
            $diag = $res.Output[0]
            Write-LogEntry -Level 'SUCCESS' -Message "Diagnostico completado: ADB en $($diag.AdbPath)"
            Write-LogEntry -Level 'INFO' -Message "Dispositivos detectados: $(@($diag.Devices).Count)"
            Show-NotificationBanner -Type 'SUCCESS' -Title "Diagnostico Completado" -Message "Entorno ADB analizado con exito."
        }
    }
}

# =============================================================================
# INICIALIZACION Y EVENTO SHOWN
# =============================================================================
$form.Add_Shown({
    $form.Activate()
    $form.BringToFront()
    Write-LogEntry -Level 'INFO' -Message 'Bienvenido a Android ADB Tools. Inicializando interfaz y escaneo inteligente...'
    Refresh-DeviceStatus -Silent
    Invoke-UsbAutoDetection

    $script:UsbAutoScanTimer = New-Object System.Windows.Forms.Timer
    $script:UsbAutoScanTimer.Interval = 2500
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

# Lanzar la aplicacion
[System.Windows.Forms.Application]::Run($form)
