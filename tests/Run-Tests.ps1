# =============================================================================
# EJECUTOR CENTRAL DE PRUEBAS AUTOMATIZADAS (Test Runner)
# =============================================================================
param(
    [switch]$Passthru
)

$ErrorActionPreference = 'Stop'
$testsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $testsDir ".."))

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         EJECUTANDO SUITE DE PRUEBAS AUTOMATIZADAS          " -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Directorio objetivo: $repoRoot`n" -ForegroundColor Gray

# Verificar Pester
$hasPester = Get-Module -ListAvailable -Name Pester
if (-not $hasPester) {
    Write-Host "WARN: El modulo Pester no esta instalado. Instalando o ejecutando analisis directo..." -ForegroundColor Yellow
}

$startTime = [System.Diagnostics.Stopwatch]::StartNew()
$testFiles = Get-ChildItem -Path $testsDir -Filter "*.Tests.ps1"

$totalTests = 0
$passedTests = 0
$failedTests = 0
$failureDetails = @()

foreach ($tf in $testFiles) {
    Write-Host "📄 Ejecutando suite: $($tf.Name)..." -ForegroundColor Cyan

    try {
        if ($hasPester) {
            # Usar Pester
            $pesterRes = Invoke-Pester -Script $tf.FullName -PassThru -EnableExit:$false
            $totalTests += $pesterRes.TotalCount
            $passedTests += $pesterRes.PassedCount
            $failedTests += $pesterRes.FailedCount
            if ($pesterRes.FailedCount -gt 0) {
                $failureDetails += "Fallo en $($tf.Name)"
            }
        } else {
            # Ejecucion directa de fallback
            & $tf.FullName
            $passedTests++
            $totalTests++
        }
    } catch {
        $failedTests++
        $totalTests++
        $failureDetails += "$($tf.Name): $($_.Exception.Message)"
        Write-Host "❌ ERROR en $($tf.Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

$startTime.Stop()
$elapsed = [math]::Round($startTime.Elapsed.TotalSeconds, 2)

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "                  RESUMEN DE RESULTADOS                     " -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Pruebas Totales:  $totalTests" -ForegroundColor White
Write-Host "Pruebas Pasadas:  $passedTests" -ForegroundColor Green

if ($failedTests -gt 0) {
    Write-Host "Pruebas Fallidas: $failedTests" -ForegroundColor Red
    Write-Host "`nDetalles de fallos:" -ForegroundColor Red
    foreach ($fd in $failureDetails) {
        Write-Host " - $fd" -ForegroundColor Red
    }
    Write-Host "`nTIEMPO TOTAL: ${elapsed}s | ESTADO: ❌ CON ERRORES`n" -ForegroundColor Red
    if (-not $Passthru) { exit 1 }
} else {
    Write-Host "Pruebas Fallidas: 0" -ForegroundColor Green
    Write-Host "`nTIEMPO TOTAL: ${elapsed}s | ESTADO: ✅ TODAS LAS PRUEBAS PASARON EXITOSAMENTE`n" -ForegroundColor Green
    if (-not $Passthru) { exit 0 }
}
