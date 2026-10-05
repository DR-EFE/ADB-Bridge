# android_reconnect.ps1 - Windows Version CORREGIDA
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   RECONEXION RAPIDA ANDROID           " -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verificar archivo de configuracion
$configFile = "$env:USERPROFILE\AndroidTools\android_config.txt"
if (-not (Test-Path $configFile)) {
    Write-Host "ERROR: No hay configuracion guardada." -ForegroundColor Red
    Write-Host "   Ejecuta primero: android_setup.ps1 (con USB)" -ForegroundColor Yellow
    pause
    exit 1
}

# Leer IP del archivo de configuracion
$ip = $null
Get-Content $configFile | ForEach-Object {
    if ($_ -match 'ANDROID_IP="(.+)"') {
        $ip = $matches[1]
    }
}

if (-not $ip) {
    Write-Host "ERROR: No se encontro la IP en la configuracion" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "Intentando conectar a: ${ip}:5555" -ForegroundColor White
Write-Host ""

# Intentar conexion
$maxAttempts = 3
for ($i = 1; $i -le $maxAttempts; $i++) {
    Write-Host "Intento $i de $maxAttempts..." -ForegroundColor Gray
    adb connect "${ip}:5555" 2>$null
    Start-Sleep -Seconds 2
    
    $devices = adb devices
    if ($devices -match "${ip}:5555") {
        Write-Host ""
        Write-Host "OK: ¡Conectado exitosamente!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Comandos disponibles:" -ForegroundColor White
        Write-Host "   adb shell ls /sdcard/      - Listar archivos" -ForegroundColor Gray
        Write-Host ""
        
        # Verificar si esta autorizado
        Write-Host "Verificando autorizacion..." -ForegroundColor Cyan
        $test = adb shell "echo test" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "OK: Dispositivo autorizado y funcionando" -ForegroundColor Green
        } else {
            Write-Host "ADVERTENCIA: Conectado pero no autorizado" -ForegroundColor Yellow
            Write-Host "   Revisa el celular para autorizar la conexion" -ForegroundColor White
        }
        
        pause
        exit 0
    }
    
    if ($i -lt $maxAttempts) {
        Write-Host "Esperando 3 segundos antes de reintentar..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
    }
}

Write-Host ""
Write-Host "ERROR: No se pudo conectar despues de $maxAttempts intentos" -ForegroundColor Red
Write-Host ""
Write-Host "SOLUCION DE PROBLEMAS:" -ForegroundColor Yellow
Write-Host "   1. Verifica que el celular esta ENCENDIDO" -ForegroundColor White
Write-Host "   2. Asegurate que esta conectado a la misma red WiFi" -ForegroundColor White
Write-Host "   3. El WiFi del celular debe estar ACTIVADO" -ForegroundColor White
Write-Host "   4. Prueba reiniciar el celular" -ForegroundColor White
Write-Host "   5. Si nada funciona, ejecuta el setup con USB de nuevo" -ForegroundColor White
Write-Host ""

# Verificar si la IP responde al ping
Write-Host "Verificando red..." -ForegroundColor Cyan
if (Test-Connection -ComputerName $ip -Count 1 -Quiet) {
    Write-Host "   La IP $ip responde al ping" -ForegroundColor Green
    Write-Host "   El problema es con ADB, no con la red" -ForegroundColor Yellow
} else {
    Write-Host "   La IP $ip NO responde al ping" -ForegroundColor Red
    Write-Host "   Verifica que el celular esta en la misma red" -ForegroundColor White
}

Write-Host ""
pause
exit 1