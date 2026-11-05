# =====================================================================
# test_photo_metadata.ps1
# Pruebas de Integracion y Validacion del Modulo android_photo_metadata.ps1
# =====================================================================

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$scriptRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$metadataScript = Join-Path $scriptRoot 'android_photo_metadata.ps1'

if (-not (Test-Path $metadataScript)) {
    throw "No se encontro el script principal: $metadataScript"
}

$tempBase = Join-Path $env:TEMP ("MetadataTest_" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempBase -Force | Out-Null

$testImagesDir = Join-Path $tempBase 'test_samples'
New-Item -ItemType Directory -Path $testImagesDir -Force | Out-Null

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " INICIANDO PRUEBAS DE SUITE: android_photo_metadata.ps1" -ForegroundColor Cyan
Write-Host " Directorio temporal: $tempBase" -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------
# 1. GENERAR IMAGENES DE PRUEBA
# ---------------------------------------------------------------------
$img1 = Join-Path $testImagesDir "sample_alpha.jpg"
$img2 = Join-Path $testImagesDir "sample_beta.jpg"
$img3 = Join-Path $testImagesDir "sample_gamma.jpg"

foreach ($img in @($img1, $img2, $img3)) {
    $bmp = New-Object System.Drawing.Bitmap 320, 240
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::FromArgb(40, 90, 150))
    $g.Dispose()
    $bmp.Save($img, [System.Drawing.Imaging.ImageFormat]::Jpeg)
    $bmp.Dispose()
}

$testZip = Join-Path $tempBase "photos_bundle.zip"
Compress-Archive -Path $img1, $img2 -DestinationPath $testZip -Force

Write-Host "[OK] Imagenes y archivo ZIP de prueba generados." -ForegroundColor Green

$failedTests = 0

# ---------------------------------------------------------------------
# PRUEBA 1: MODO ARCHIVO COMPRIMIDO (.ZIP) Y DIRECTORIO DEDICADO
# ---------------------------------------------------------------------
Write-Host "`n--- PRUEBA 1: Modo Archivo Comprimido (archive_extracted/) ---" -ForegroundColor Yellow
$outDirArchive = Join-Path $tempBase "Out_Archive"

try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $metadataScript `
        -ArchivePath $testZip `
        -OutputDir $outDirArchive `
        -NoOpenBrowser `
        -NoZip

    # Verificar creacion obligatoria del directorio dedicado archive_extracted/
    $stagingDir = Join-Path $outDirArchive "archive_extracted"
    if (-not (Test-Path $stagingDir)) {
        throw "FALLO: El directorio dedicado 'archive_extracted' NO fue creado."
    }
    Write-Host "  [+] Directorio dedicado 'archive_extracted/' verificado correctamente." -ForegroundColor Green

    # Verificar que las imagenes se hayan extraido en staging y luego en source
    $stagedFiles = Get-ChildItem -Path $stagingDir -Recurse -File
    if ($stagedFiles.Count -lt 2) {
        throw "FALLO: Menos de 2 archivos en archive_extracted ($($stagedFiles.Count))."
    }
    Write-Host "  [+] Archivos extraidos en staging: $($stagedFiles.Count)" -ForegroundColor Green

    # Verificar artefactos principales
    $requiredArtifacts = @(
        'raw_metadata.csv',
        'normalized_metadata.json',
        'normalized_metadata.csv',
        'analysis_summary.json',
        'map.html',
        'dashboard.html',
        'evidence_manifest.json',
        'README.txt',
        'module.log'
    )

    foreach ($art in $requiredArtifacts) {
        $p = Join-Path $outDirArchive $art
        if (-not (Test-Path $p)) {
            throw "FALLO: Artefacto ausente: $art"
        }
        $sz = (Get-Item $p).Length
        if ($sz -le 0) {
            throw "FALLO: Artefacto vacio: $art"
        }
    }
    Write-Host "  [+] Todos los artefactos de evidencia requeridos existen y no estan vacios." -ForegroundColor Green

    # Validar que los placeholders HTML se hayan inyectado
    $mapHtml = [System.IO.File]::ReadAllText((Join-Path $outDirArchive "map.html"))
    if ($mapHtml -like '*/*__PHOTOS_JSON__*/*') {
        throw "FALLO: map.html conserva el placeholder /*__PHOTOS_JSON__*/ sin reemplazar."
    }

    $dashHtml = [System.IO.File]::ReadAllText((Join-Path $outDirArchive "dashboard.html"))
    if ($dashHtml -like '*/*__PHOTOS_JSON__*/*' -or $dashHtml -like '*/*__SUMMARY_JSON__*/*' -or $dashHtml -like '*/*__MANIFEST_JSON__*/*') {
        throw "FALLO: dashboard.html conserva placeholders sin reemplazar."
    }
    Write-Host "  [+] Plantillas HTML compiladas e inyectadas correctamente." -ForegroundColor Green

    # Validar JSONs
    $manifestObj = Get-Content (Join-Path $outDirArchive "evidence_manifest.json") -Raw | ConvertFrom-Json
    if (-not $manifestObj.SessionId -or $manifestObj.Artifacts.Count -lt 6) {
        throw "FALLO: evidence_manifest.json tiene datos invalidos o faltan hashes."
    }
    Write-Host "  [+] Manifiesto de evidencia valido con $($manifestObj.Artifacts.Count) hashes SHA-256." -ForegroundColor Green

    $summaryObj = Get-Content (Join-Path $outDirArchive "analysis_summary.json") -Raw | ConvertFrom-Json
    if ($summaryObj.TotalImages -lt 2) {
        throw "FALLO: TotalImages en analysis_summary.json es $($summaryObj.TotalImages)."
    }
    Write-Host "  [+] Resumen de analisis valido (TotalImages: $($summaryObj.TotalImages))." -ForegroundColor Green

    Write-Host "[PASS] PRUEBA 1 SUPERADA." -ForegroundColor Green
} catch {
    Write-Host "ERROR EN PRUEBA 1: $($_.Exception.Message)" -ForegroundColor Red
    $failedTests++
}

# ---------------------------------------------------------------------
# PRUEBA 2: MODO IMAGEN UNICA
# ---------------------------------------------------------------------
Write-Host "`n--- PRUEBA 2: Modo Imagen Unica ---" -ForegroundColor Yellow
$outDirSingle = Join-Path $tempBase "Out_Single"

try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $metadataScript `
        -ImagePath $img3 `
        -OutputDir $outDirSingle `
        -NoOpenBrowser `
        -NoZip

    $manifestSingle = Get-Content (Join-Path $outDirSingle "evidence_manifest.json") -Raw | ConvertFrom-Json
    if ($manifestSingle.SourceType -ne 'SingleImage' -or $manifestSingle.TotalImages -ne 1) {
        throw "FALLO: Modo imagen unica no registro TotalImages = 1 (SourceType: $($manifestSingle.SourceType))."
    }

    $normSingle = @(Get-Content (Join-Path $outDirSingle "normalized_metadata.json") -Raw | ConvertFrom-Json)
    if ($normSingle.Count -ne 1) {
        throw "FALLO: normalized_metadata.json no contiene exactamente 1 elemento (Obtenidos: $($normSingle.Count))."
    }
    Write-Host "  [+] Modo imagen unica procesado con exito (Total: 1)." -ForegroundColor Green

    Write-Host "[PASS] PRUEBA 2 SUPERADA." -ForegroundColor Green
} catch {
    Write-Host "ERROR EN PRUEBA 2: $($_.Exception.Message)" -ForegroundColor Red
    $failedTests++
}

# ---------------------------------------------------------------------
# PRUEBA 3: MODO CARPETA LOCAL Y GENERACION DE THUMBNAILS
# ---------------------------------------------------------------------
Write-Host "`n--- PRUEBA 3: Modo Carpeta Local y Miniaturas ---" -ForegroundColor Yellow
$outDirFolder = Join-Path $tempBase "Out_Folder"

try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $metadataScript `
        -FolderPath $testImagesDir `
        -OutputDir $outDirFolder `
        -NoOpenBrowser `
        -NoZip

    $thumbs = Get-ChildItem -Path (Join-Path $outDirFolder "thumbs") -Filter "*.jpg"
    if ($thumbs.Count -lt 3) {
        throw "FALLO: Se esperaban al menos 3 miniaturas en thumbs/, se encontraron $($thumbs.Count)."
    }
    Write-Host "  [+] Miniaturas generadas correctamente: $($thumbs.Count)" -ForegroundColor Green

    $summaryFolder = Get-Content (Join-Path $outDirFolder "analysis_summary.json") -Raw | ConvertFrom-Json
    if ($summaryFolder.TotalImages -ne 3) {
        throw "FALLO: Se esperaban 3 imagenes procesadas en la carpeta, se obtuvieron $($summaryFolder.TotalImages)."
    }
    Write-Host "  [+] Carpeta procesada con exito (Total: 3 imagenes)." -ForegroundColor Green

    Write-Host "[PASS] PRUEBA 3 SUPERADA." -ForegroundColor Green
} catch {
    Write-Host "ERROR EN PRUEBA 3: $($_.Exception.Message)" -ForegroundColor Red
    $failedTests++
}

# ---------------------------------------------------------------------
# LIMPIEZA
# ---------------------------------------------------------------------
try {
    Remove-Item -LiteralPath $tempBase -Recurse -Force -ErrorAction SilentlyContinue
} catch { }

Write-Host "`n========================================================" -ForegroundColor Cyan
if ($failedTests -eq 0) {
    Write-Host " TODAS LAS PRUEBAS DE METADATOS PASARON CON EXITO (0 fallos)" -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Cyan
    exit 0
} else {
    Write-Host " HUBIERON $failedTests PRUEBAS FALLIDAS." -ForegroundColor Red
    Write-Host "========================================================" -ForegroundColor Cyan
    exit 1
}
