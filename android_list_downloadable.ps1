# android_download_all.ps1 - Descargar carpetas completas (INCLUYE CAPTURAS)
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
$script:AndroidDevice = Get-SingleReadyDevice
function adb {
    param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments)
    if ($Arguments.Count -eq 1 -and [string]$Arguments[0] -ceq 'devices') { return Get-AdbDevices }
    return Invoke-Adb -Serial $script:AndroidDevice.Serial -Arguments ([string[]]$Arguments)
}

function Download-AndroidFolder {
    param(
        [string]$remotePath,
        [string]$localName,
        [switch]$recursive
    )
    
    $downloadDir = "$env:USERPROFILE\Downloads\AndroidBackup\$localName"
    
    if (-not (Test-Path $downloadDir)) {
        New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
    }
    
    Write-Host ""
    Write-Host "Descargando: $remotePath" -ForegroundColor Yellow
    Write-Host "Destino: $downloadDir" -ForegroundColor Gray
    Write-Host ""
    
    # Usar adb pull con -a para preservar atributos
    $result = adb pull -a "$remotePath/" "$downloadDir/" 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK: Descarga completada!" -ForegroundColor Green
        
        # Mostrar tamaño
        $totalSize = (Get-ChildItem $downloadDir -Recurse -ErrorAction SilentlyContinue | 
                     Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
        
        if ($totalSize -gt 0) {
            $sizeGB = [math]::Round($totalSize / 1GB, 2)
            $sizeMB = [math]::Round($totalSize / 1MB, 2)
            Write-Host "Tamaño total: $sizeMB MB ($sizeGB GB)" -ForegroundColor Cyan
        }
        
        # Contar archivos
        $fileCount = (Get-ChildItem $downloadDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Host "Archivos descargados: $fileCount" -ForegroundColor Cyan
    } else {
        Write-Host "ERROR: No se pudo descargar completamente" -ForegroundColor Red
        Write-Host "Algunas subcarpetas pueden estar protegidas" -ForegroundColor Yellow
    }
    
    return $downloadDir
}

function Show-Menu {
    Clear-Host
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   DESCARGAR CARPETAS COMPLETAS         " -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Verificar conexion
    if ($null -eq $script:AndroidDevice) {
        Write-Host "ERROR: No hay dispositivo conectado" -ForegroundColor Red
        return $false
    }
    
    Write-Host "OK: Dispositivo conectado" -ForegroundColor Green
    Write-Host ""
    Write-Host "SELECCIONA QUE DESCARGAR:" -ForegroundColor White
    Write-Host ""
    Write-Host "  [MULTIMEDIA]" -ForegroundColor Cyan
    Write-Host "  1. Fotos y videos (DCIM)" -ForegroundColor Green
    Write-Host "  2. Capturas de pantalla (Screenshots)" -ForegroundColor Green
    Write-Host "  3. Musica (Music)" -ForegroundColor Green
    Write-Host "  4. Videos (Movies)" -ForegroundColor Green
    Write-Host "  5. Imagenes (Pictures)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  [DOCUMENTOS]" -ForegroundColor Cyan
    Write-Host "  6. Descargas (Download)" -ForegroundColor Green
    Write-Host "  7. Documentos (Documents)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  [APPS Y DATOS]" -ForegroundColor Cyan
    Write-Host "  8. Android/media (WhatsApp, Telegram)" -ForegroundColor Green
    Write-Host "  9. Android/obb (Archivos de juegos)" -ForegroundColor Green
    Write-Host "  10. Android/data (Datos apps - limitado)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  [COMPLETO]" -ForegroundColor Cyan
    Write-Host "  11. TODO multimedia (Fotos + Capturas + Videos)" -ForegroundColor Green
    Write-Host "  12. TODO completo /sdcard/ (MUY LENTO)" -ForegroundColor Red
    Write-Host "  13. Carpeta especifica" -ForegroundColor White
    Write-Host "  14. Salir" -ForegroundColor Gray
    Write-Host ""
    
    return $true
}

# Funcion especifica para capturas de pantalla
function Download-Screenshots {
    Write-Host ""
    Write-Host "DESCARGANDO CAPTURAS DE PANTALLA" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor DarkGray
    
    # Posibles ubicaciones de capturas de pantalla
    $screenshotPaths = @(
        "/sdcard/DCIM/Screenshots",
        "/sdcard/Pictures/Screenshots",
        "/sdcard/Screenshots",
        "/sdcard/DCIM/ScreenCaptures",
        "/sdcard/Pictures/ScreenCaptures"
    )
    
    $found = $false
    foreach ($path in $screenshotPaths) {
        Write-Host "Buscando en: $path" -ForegroundColor Gray
        $exists = adb shell "[ -d '$path' ] && echo 'yes' || echo 'no'" 2>$null
        
        if ($exists -match "yes") {
            Write-Host "  [ENCONTRADO] Descargando..." -ForegroundColor Green
            Download-AndroidFolder -remotePath $path -localName "Screenshots"
            $found = $true
            break
        }
    }
    
    if (-not $found) {
        Write-Host "No se encontro carpeta de capturas" -ForegroundColor Yellow
        Write-Host "Buscando archivos .png .jpg en todo el dispositivo..." -ForegroundColor Gray
        
        # Buscar archivos de captura por nombre
        $screenshots = adb shell "find /sdcard -name '*screenshot*.png' -o -name '*screenshot*.jpg' -o -name '*Screenshot*.png' -o -name '*captura*.png' 2>/dev/null | head -20" 2>$null
        
        if ($screenshots -and $screenshots -ne "") {
            Write-Host "Archivos de captura encontrados:" -ForegroundColor Green
            $screenshotsDir = "$env:USERPROFILE\Downloads\AndroidBackup\Screenshots_Disperas"
            New-Item -ItemType Directory -Path $screenshotsDir -Force | Out-Null
            
            foreach ($file in ($screenshots -split "`r`n")) {
                if ($file -and $file -ne "") {
                    Write-Host "  Descargando: $(Split-Path $file -Leaf)" -ForegroundColor Gray
                    adb pull "$file" "$screenshotsDir\" 2>$null
                }
            }
            Write-Host "Capturas guardadas en: $screenshotsDir" -ForegroundColor Green
        } else {
            Write-Host "No se encontraron capturas de pantalla" -ForegroundColor Red
        }
    }
}

# Menu principal
if (-not (Show-Menu)) { exit }

$option = Read-Host "Selecciona opcion (1-14)"

switch ($option) {
    "1" {
        Download-AndroidFolder -remotePath "/sdcard/DCIM" -localName "Fotos_Videos_DCIM"
    }
    
    "2" {
        Download-Screenshots
    }
    
    "3" {
        Download-AndroidFolder -remotePath "/sdcard/Music" -localName "Musica"
    }
    
    "4" {
        Download-AndroidFolder -remotePath "/sdcard/Movies" -localName "Videos_Movies"
    }
    
    "5" {
        Download-AndroidFolder -remotePath "/sdcard/Pictures" -localName "Imagenes_Pictures"
    }
    
    "6" {
        Download-AndroidFolder -remotePath "/sdcard/Download" -localName "Descargas"
    }
    
    "7" {
        Download-AndroidFolder -remotePath "/sdcard/Documents" -localName "Documentos"
    }
    
    "8" {
        Download-AndroidFolder -remotePath "/sdcard/Android/media" -localName "Android_media"
    }
    
    "9" {
        Download-AndroidFolder -remotePath "/sdcard/Android/obb" -localName "Android_obb"
    }
    
    "10" {
        Write-Host ""
        Write-Host "DESCARGANDO Android/data/" -ForegroundColor Yellow
        Write-Host "NOTA: En Android 11+, muchas apps protegen esta carpeta" -ForegroundColor Red
        Write-Host ""
        
        $confirm = Read-Host "Continuar? (s/n)"
        if ($confirm -eq 's') {
            Download-AndroidFolder -remotePath "/sdcard/Android/data" -localName "Android_data"
        }
    }
    
    "11" {
        Write-Host ""
        Write-Host "DESCARGANDO TODO EL MULTIMEDIA" -ForegroundColor Cyan
        Write-Host "========================================" -ForegroundColor DarkGray
        
        # Fotos y videos
        Download-AndroidFolder -remotePath "/sdcard/DCIM" -localName "01_Fotos_Videos"
        
        # Capturas de pantalla
        Download-Screenshots
        
        # Musica
        Download-AndroidFolder -remotePath "/sdcard/Music" -localName "03_Musica"
        
        # Videos
        Download-AndroidFolder -remotePath "/sdcard/Movies" -localName "04_Videos"
        
        # Imagenes
        Download-AndroidFolder -remotePath "/sdcard/Pictures" -localName "05_Imagenes"
        
        Write-Host ""
        Write-Host "TODO EL MULTIMEDIA HA SIDO DESCARGADO!" -ForegroundColor Green
        Write-Host "Ubicacion: $env:USERPROFILE\Downloads\AndroidBackup\" -ForegroundColor Cyan
    }
    
    "12" {
        Write-Host ""
        Write-Host "ADVERTENCIA: Descargar TODO /sdcard/ puede tomar HORAS" -ForegroundColor Red
        Write-Host "Espacio necesario: Al menos el tamaño de tu almacenamiento" -ForegroundColor Yellow
        Write-Host ""
        
        $confirm = Read-Host "REALMENTE quieres descargar todo? (escribe 'TODO' para confirmar)"
        if ($confirm -eq 'TODO') {
            Write-Host ""
            Write-Host "Iniciando descarga completa..." -ForegroundColor Red
            Write-Host "Esto puede tomar varias horas..." -ForegroundColor Yellow
            
            Download-AndroidFolder -remotePath "/sdcard" -localName "sdcard_COMPLETO"
        }
    }
    
    "13" {
        Write-Host ""
        Write-Host "CARPETA ESPECIFICA" -ForegroundColor Cyan
        Write-Host "Ejemplos de rutas utiles:" -ForegroundColor Gray
        Write-Host "  /sdcard/DCIM/Screenshots - Capturas" -ForegroundColor Gray
        Write-Host "  /sdcard/WhatsApp/Media - Multimedia WhatsApp" -ForegroundColor Gray
        Write-Host "  /sdcard/Telegram/Telegram Images - Imagenes Telegram" -ForegroundColor Gray
        Write-Host "  /sdcard/Download - Descargas" -ForegroundColor Gray
        Write-Host "  /sdcard/Music - Musica" -ForegroundColor Gray
        Write-Host ""
        
        $customPath = Read-Host "Ruta completa"
        $customName = Read-Host "Nombre para la carpeta destino"
        
        if ($customPath -and $customName) {
            Download-AndroidFolder -remotePath $customPath -localName $customName
        }
    }
    
    "14" {
        Write-Host "Saliendo..." -ForegroundColor Cyan
        exit
    }
    
    default {
        Write-Host "Opcion invalida" -ForegroundColor Red
    }
}

Write-Host ""
Read-Host "Presiona Enter para salir"
