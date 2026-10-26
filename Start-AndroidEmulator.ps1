param(
    [string]$Avd = 'Pixel_9_Pro',
    [int]$BootTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class EmulatorWindow {
    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr handle, int x, int y, int width, int height, bool repaint);
}
'@

$emulator = (Get-Command emulator -ErrorAction Stop).Source
$adb = (Get-Command adb -ErrorAction Stop).Source

function Get-EmulatorWindow {
    Get-Process | Where-Object {
        $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like "Android Emulator - ${Avd}:*"
    } | Select-Object -First 1
}

$window = Get-EmulatorWindow
if (-not $window) {
    Start-Process -FilePath $emulator -ArgumentList @('-avd', $Avd) -WindowStyle Normal | Out-Null
    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 500
        $window = Get-EmulatorWindow
    } until ($window -or (Get-Date) -ge $deadline)
    if (-not $window) { throw "No apareció la ventana del emulador $Avd." }
}

$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$height = [Math]::Min(740, [Math]::Max(400, $area.Height - 40))
$width = [Math]::Round($height * 0.473)
$x = $area.Left + [Math]::Max(0, [Math]::Floor(($area.Width - $width) / 2))
$y = $area.Top + [Math]::Max(0, [Math]::Floor(($area.Height - $height) / 2))
if (-not [EmulatorWindow]::MoveWindow($window.MainWindowHandle, $x, $y, $width, $height, $true)) {
    throw "No se pudo colocar la ventana del emulador dentro de la pantalla."
}
Write-Host "Ventana de $Avd abierta en ${x},${y} (${width}x${height})."

$serial = [regex]::Match($window.MainWindowTitle, ':(\d+)$').Groups[1].Value
if (-not $serial) { throw 'No se pudo identificar el puerto ADB del emulador.' }
$serial = "emulator-$serial"
$deadline = (Get-Date).AddSeconds($BootTimeoutSeconds)
do {
    $state = ''
    try { $state = & $adb -s $serial get-state 2>$null } catch { }
    if ($LASTEXITCODE -eq 0 -and $state -eq 'device') {
        $booted = ''
        try { $booted = & $adb -s $serial shell getprop sys.boot_completed 2>$null } catch { }
        if ($LASTEXITCODE -eq 0 -and $booted -eq '1') {
            Write-Host "$serial listo: Android termino de arrancar."
            exit 0
        }
    }
    Start-Sleep -Seconds 2
} until ((Get-Date) -ge $deadline)

throw "$serial no terminó de arrancar en $BootTimeoutSeconds segundos. Revise 'adb devices' y el registro del emulador."
