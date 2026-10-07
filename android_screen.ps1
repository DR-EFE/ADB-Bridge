# Captura y grabacion de pantalla Android.
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'android_screen.ps1' -Message 'Inicio de herramientas de pantalla.'

try {
    $script:AndroidDevice = Select-CurrentAdbDevice -Persist
    if (-not (Test-AdbDeviceReady -Device $script:AndroidDevice -AutoReconnect $true)) {
        throw 'El dispositivo seleccionado no esta listo despues de la reconexion.'
    }
} catch {
    Write-AdbLog -Level ERROR -Source 'android_screen.ps1' -Message $_.Exception.Message
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}

function Show-ScreenMenu {
    Write-Host ''
    Write-Host '========================================' -ForegroundColor Cyan
    Write-Host ' ANDROID SCREEN' -ForegroundColor Yellow
    Write-Host '========================================' -ForegroundColor Cyan
    Write-Host '1. Capturar screenshot'
    Write-Host '2. Grabar pantalla'
    Write-Host '3. Lanzar scrcpy'
    Write-Host '4. Salir'
}

try {
    while ($true) {
        Show-ScreenMenu
        switch (Read-Host 'Selecciona una opcion') {
            '1' {
                $open = (Read-Host 'Abrir la imagen al terminar? (S/N)') -match '^(s|si|y|yes)$'
                try {
                    $file = Capture-AdbScreenshot -Device $script:AndroidDevice -OpenAfterCapture:$open
                    Write-Host ("OK: captura guardada en {0}" -f $file.FullName) -ForegroundColor Green
                } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red }
                Read-Host 'Presiona Enter para continuar' | Out-Null
            }
            '2' {
                $secondsText = Read-Host 'Duracion maxima en segundos [180]'
                $seconds = 180
                if (-not [string]::IsNullOrWhiteSpace($secondsText)) {
                    if (-not [int]::TryParse($secondsText,[ref]$seconds) -or $seconds -lt 1 -or $seconds -gt 86400) {
                        Write-Host 'ERROR: indica entre 1 y 86400 segundos.' -ForegroundColor Red
                        continue
                    }
                }
                try {
                    Write-Host 'Presiona Ctrl+C para detener antes del limite.' -ForegroundColor Yellow
                    $file = Start-AdbScreenRecording -Device $script:AndroidDevice -DurationSeconds $seconds
                    Write-Host ("OK: video guardado en {0}" -f $file.FullName) -ForegroundColor Green
                } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red }
                Read-Host 'Presiona Enter para continuar' | Out-Null
            }
            '3' {
                try { Start-AdbScrcpy -Device $script:AndroidDevice | Out-Null; Write-Host 'scrcpy iniciado.' -ForegroundColor Green }
                catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red }
                Read-Host 'Presiona Enter para continuar' | Out-Null
            }
            '4' { break }
            default { Write-Host 'ERROR: opcion invalida.' -ForegroundColor Red }
        }
    }
    Write-AdbLog -Level SUCCESS -Source 'android_screen.ps1' -Message 'Fin de herramientas de pantalla.'
} catch {
    Write-AdbLog -Level ERROR -Source 'android_screen.ps1' -Message $_.Exception.Message
    throw
}
