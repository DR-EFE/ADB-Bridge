# Diagnostico completo del entorno ADB.
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
try { Invoke-AdbDiagnostics | Out-Null } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red; exit 1 }
