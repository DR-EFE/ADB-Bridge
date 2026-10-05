# Copia y ejecuta este script:
Write-Host "=== SOLUCIONANDO PROBLEMA UNAUTHORIZED ===" -ForegroundColor Yellow
Write-Host ""

Write-Host "1. Reiniciando ADB..." -ForegroundColor Cyan
adb kill-server
Start-Sleep 2
adb start-server
Start-Sleep 2

Write-Host "2. Conectando..." -ForegroundColor Cyan
adb connect 192.168.1.70:5555
Start-Sleep 2

Write-Host "3. Verificando estado..." -ForegroundColor Cyan
adb devices

Write-Host ""
Write-Host "=== INSTRUCCIONES IMPORTANTES ===" -ForegroundColor Red
Write-Host "EN TU CELULAR DEBE APARECER UN MENSAJE:" -ForegroundColor Yellow
Write-Host ""
Write-Host "    [ ] Permitir depuración USB?" -ForegroundColor White
Write-Host "    [✓] Siempre permitir desde este equipo" -ForegroundColor Green  
Write-Host "    [  OK  ]     [ Cancelar ]" -ForegroundColor White
Write-Host ""
Write-Host "1. MARCA la casilla 'Siempre permitir'" -ForegroundColor Green
Write-Host "2. TOCA 'OK' o 'Permitir'" -ForegroundColor Green
Write-Host "3. Espera 5 segundos" -ForegroundColor Green
Write-Host ""

Write-Host "Despues de autorizar en el celular, presiona Enter aqui..."
Read-Host

Write-Host "4. Verificando conexion autorizada..." -ForegroundColor Cyan
adb devices

if (adb devices -match "device") {
    Write-Host "✅ ¡CONEXION AUTORIZADA EXITOSAMENTE!" -ForegroundColor Green
} else {
    Write-Host "❌ Aun no autorizado. Prueba conectar por USB primero." -ForegroundColor Red
}