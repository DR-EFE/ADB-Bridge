# Punto de entrada compatible con SETUP.bat.
# La implementacion actual vive en android_setup_v2.ps1.

$modernSetup = Join-Path -Path $PSScriptRoot -ChildPath 'android_setup_v2.ps1'
if (-not (Test-Path -LiteralPath $modernSetup)) {
    Write-Error "No se encontro el script de configuracion: $modernSetup"
    exit 1
}

& $modernSetup @args
exit $LASTEXITCODE
