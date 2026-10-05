# android_transfer.ps1 - Windows Version CORREGIDA
function Show-Menu {
    Clear-Host
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   ANDROID TRANSFER INTERFACE (Windows)   " -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Verificar conexion
    $devices = adb devices
    if ($devices -match "device`$") {
        Write-Host "OK: CONECTADO VIA WIFI" -ForegroundColor Green
        
        # Obtener informacion del dispositivo
        $model = adb shell getprop ro.product.model 2>$null
        $android = adb shell getprop ro.build.version.release 2>$null
        
        Write-Host "   Modelo: $model" -ForegroundColor White
        Write-Host "   Android: $android" -ForegroundColor White
    } else {
        Write-Host "ERROR: NO CONECTADO" -ForegroundColor Red
        Write-Host "   Ejecuta primero: android_setup.ps1" -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
    Write-Host "  1. Transferir FOTOS/VIDEOS" -ForegroundColor White
    Write-Host "  2. Transferir DOCUMENTOS" -ForegroundColor White
    Write-Host "  3. Subir archivos al celular" -ForegroundColor White
    Write-Host "  4. Backup COMPLETO" -ForegroundColor White
    Write-Host "  5. Buscar archivos" -ForegroundColor White
    Write-Host "  6. Ver espacio disponible" -ForegroundColor White
    Write-Host "  7. Reconectar" -ForegroundColor White
    Write-Host "  8. Salir" -ForegroundColor White
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
    Write-Host ""
}

function Transfer-Photos {
    Write-Host ""
    Write-Host "TRANSFERIR FOTOS Y VIDEOS" -ForegroundColor Green
    Write-Host "--------------------------" -ForegroundColor DarkGray
    
    Write-Host "1. Solo fotos recientes (ultimos 7 dias)"
    Write-Host "2. Todas las fotos"
    Write-Host "3. Solo videos"
    Write-Host "4. Fotos y videos completos"
    Write-Host "5. Volver al menu principal"
    Write-Host ""
    $media_opt = Read-Host "Selecciona opcion"
    
    $backupDir = "$env:USERPROFILE\Downloads\AndroidBackup\$(Get-Date -Format 'yyyyMMdd')"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    switch ($media_opt) {
        "1" {
            Write-Host "Descargando fotos recientes..." -ForegroundColor Yellow
            adb pull /sdcard/DCIM/Camera/ "$backupDir\" 2>$null
            Write-Host "OK: Descargadas a: $backupDir" -ForegroundColor Green
        }
        "2" {
            Write-Host "Descargando todas las fotos..." -ForegroundColor Yellow
            adb pull /sdcard/DCIM/ "$backupDir\" 2>$null
            Write-Host "OK: Descargadas a: $backupDir" -ForegroundColor Green
        }
        "3" {
            Write-Host "Descargando videos..." -ForegroundColor Yellow
            adb pull /sdcard/DCIM/Camera/*.mp4 "$backupDir\" 2>$null
            adb pull /sdcard/Movies/ "$backupDir\" 2>$null
            Write-Host "OK: Descargados a: $backupDir" -ForegroundColor Green
        }
        "4" {
            Write-Host "Descargando fotos y videos completos..." -ForegroundColor Yellow
            $dcimDir = "$backupDir\DCIM"
            $moviesDir = "$backupDir\Movies"
            New-Item -ItemType Directory -Path $dcimDir -Force | Out-Null
            New-Item -ItemType Directory -Path $moviesDir -Force | Out-Null
            adb pull /sdcard/DCIM/ "$dcimDir\" 2>$null
            adb pull /sdcard/Movies/ "$moviesDir\" 2>$null
            Write-Host "OK: Descargados a: $backupDir" -ForegroundColor Green
        }
        "5" {
            return
        }
        default {
            Write-Host "ERROR: Opcion invalida" -ForegroundColor Red
            return
        }
    }
    
    # Mostrar tamano
    if (Test-Path $backupDir) {
        $size = (Get-ChildItem $backupDir -Recurse -ErrorAction SilentlyContinue | 
                Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
        if ($size -and $size -gt 0) {
            $sizeMB = [math]::Round($size / 1MB, 2)
            Write-Host "TAMANO TOTAL: ${sizeMB} MB" -ForegroundColor Cyan
        }
    }
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Transfer-Documents {
    Write-Host ""
    Write-Host "TRANSFERIR DOCUMENTOS" -ForegroundColor Green
    Write-Host "---------------------" -ForegroundColor DarkGray
    
    Write-Host "1. Descargas recientes"
    Write-Host "2. Documentos"
    Write-Host "3. PDFs"
    Write-Host "4. Todo en /sdcard/Download/"
    Write-Host "5. Volver"
    Write-Host ""
    $doc_opt = Read-Host "Selecciona opcion"
    
    $backupDir = "$env:USERPROFILE\Downloads\AndroidDocuments\$(Get-Date -Format 'yyyyMMdd')"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    switch ($doc_opt) {
        "1" {
            Write-Host "Descargando archivos recientes..." -ForegroundColor Yellow
            # Descargar archivos de los ultimos 7 dias
            adb shell "find /sdcard/Download/ -type f -mtime -7" | ForEach-Object {
                if ($_ -and $_ -ne "") {
                    adb pull $_ "$backupDir\" 2>$null
                }
            }
        }
        "2" {
            Write-Host "Descargando documentos..." -ForegroundColor Yellow
            adb pull /sdcard/Documents/ "$backupDir\" 2>$null
        }
        "3" {
            Write-Host "Descargando PDFs..." -ForegroundColor Yellow
            adb shell "find /sdcard/ -name '*.pdf' -type f" | ForEach-Object {
                if ($_ -and $_ -ne "") {
                    adb pull $_ "$backupDir\" 2>$null
                }
            }
        }
        "4" {
            Write-Host "Descargando carpeta Downloads completa..." -ForegroundColor Yellow
            adb pull /sdcard/Download/ "$backupDir\" 2>$null
        }
        "5" {
            return
        }
        default {
            Write-Host "ERROR: Opcion invalida" -ForegroundColor Red
            return
        }
    }
    
    # Contar archivos descargados
    if (Test-Path $backupDir) {
        $count = (Get-ChildItem $backupDir -File -ErrorAction SilentlyContinue | Measure-Object).Count
        if ($count -gt 0) {
            Write-Host "OK: Descargados $count documentos a: $backupDir" -ForegroundColor Green
        } else {
            Write-Host "ADVERTENCIA: No se encontraron documentos" -ForegroundColor Yellow
        }
    }
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Upload-Files {
    Write-Host ""
    Write-Host "SUBIR ARCHIVOS AL CELULAR" -ForegroundColor Green
    Write-Host "--------------------------" -ForegroundColor DarkGray
    
    Write-Host "1. Seleccionar archivo para subir"
    Write-Host "2. Subir carpeta completa"
    Write-Host "3. Ver destino actual: /sdcard/Upload/"
    Write-Host "4. Volver"
    Write-Host ""
    $upload_opt = Read-Host "Selecciona opcion"
    
    switch ($upload_opt) {
        "1" {
            Write-Host "Ruta del archivo a subir (ej: C:\Users\TuUsuario\Documentos\archivo.txt):"
            $file_path = Read-Host "Ruta"
            if (Test-Path $file_path -PathType Leaf) {
                Write-Host "Subiendo: $(Split-Path $file_path -Leaf)" -ForegroundColor Yellow
                adb push $file_path /sdcard/Upload/ 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "OK: Archivo subido exitosamente" -ForegroundColor Green
                } else {
                    Write-Host "ERROR: Error al subir el archivo" -ForegroundColor Red
                }
            } else {
                Write-Host "ERROR: Archivo no encontrado: $file_path" -ForegroundColor Red
            }
        }
        "2" {
            Write-Host "Ruta de la carpeta a subir (ej: C:\Users\TuUsuario\Documentos\Carpeta):"
            $folder_path = Read-Host "Ruta"
            if (Test-Path $folder_path -PathType Container) {
                Write-Host "Subiendo carpeta: $(Split-Path $folder_path -Leaf)" -ForegroundColor Yellow
                adb push $folder_path /sdcard/Upload/ 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "OK: Carpeta subida exitosamente" -ForegroundColor Green
                } else {
                    Write-Host "ERROR: Error al subir la carpeta" -ForegroundColor Red
                }
            } else {
                Write-Host "ERROR: Carpeta no encontrada: $folder_path" -ForegroundColor Red
            }
        }
        "3" {
            Write-Host "Contenido de /sdcard/Upload/:" -ForegroundColor Yellow
            adb shell ls -la /sdcard/Upload/ 2>$null
        }
        "4" {
            return
        }
        default {
            Write-Host "ERROR: Opcion invalida" -ForegroundColor Red
        }
    }
    
    if ($upload_opt -ne "4") {
        Write-Host ""
        Write-Host "Presiona Enter para continuar..."
        Read-Host
    }
}

function Backup-Complete {
    Write-Host ""
    Write-Host "BACKUP COMPLETO" -ForegroundColor Green
    Write-Host "---------------" -ForegroundColor DarkGray
    
    Write-Host "ADVERTENCIA: Esto puede tomar varios minutos y espacio en disco" -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "Continuar con el backup completo? (s/n)"
    
    if ($confirm -ne 's') {
        return
    }
    
    $backupName = "backup_completo_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    $backupDir = "$env:USERPROFILE\Downloads\AndroidFullBackup\$backupName"
    
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "Iniciando backup completo..." -ForegroundColor Yellow
    Write-Host "Destino: $backupDir" -ForegroundColor White
    Write-Host ""
    
    # Crear estructura de carpetas
    $folders = @("DCIM", "Download", "Documents", "Music", "Pictures")
    foreach ($folder in $folders) {
        $fullPath = "$backupDir\$folder"
        if (-not (Test-Path $fullPath)) {
            New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
        }
    }
    
    # Backup en paralelo
    Write-Host "1. Copiando fotos..." -ForegroundColor Cyan
    Start-Job -ScriptBlock { param($dir) adb pull /sdcard/DCIM/ "$dir\DCIM\" 2>$null } -ArgumentList $backupDir | Out-Null
    
    Write-Host "2. Copiando descargas..." -ForegroundColor Cyan
    Start-Job -ScriptBlock { param($dir) adb pull /sdcard/Download/ "$dir\Download\" 2>$null } -ArgumentList $backupDir | Out-Null
    
    Write-Host "3. Copiando documentos..." -ForegroundColor Cyan
    Start-Job -ScriptBlock { param($dir) adb pull /sdcard/Documents/ "$dir\Documents\" 2>$null } -ArgumentList $backupDir | Out-Null
    
    Write-Host "4. Copiando musica..." -ForegroundColor Cyan
    Start-Job -ScriptBlock { param($dir) adb pull /sdcard/Music/ "$dir\Music\" 2>$null } -ArgumentList $backupDir | Out-Null
    
    # Esperar trabajos
    Get-Job | Wait-Job | Out-Null
    Get-Job | Remove-Job
    
    Write-Host ""
    Write-Host "OK: Backup completado" -ForegroundColor Green
    Write-Host ""
    Write-Host "RESUMEN:" -ForegroundColor White
    
    foreach ($folder in $folders) {
        $folderPath = "$backupDir\$folder"
        if (Test-Path $folderPath) {
            $size = (Get-ChildItem $folderPath -Recurse -ErrorAction SilentlyContinue | 
                    Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
            if ($size -and $size -gt 0) {
                $sizeMB = [math]::Round($size / 1MB, 2)
                Write-Host "   $folder : ${sizeMB} MB" -ForegroundColor Gray
            }
        }
    }
    
    # Tamano total
    $totalSize = (Get-ChildItem $backupDir -Recurse -ErrorAction SilentlyContinue | 
                 Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
    if ($totalSize -and $totalSize -gt 0) {
        $totalSizeMB = [math]::Round($totalSize / 1MB, 2)
        Write-Host ""
        Write-Host "   TOTAL: ${totalSizeMB} MB" -ForegroundColor Cyan
    }
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

# Menu principal
while ($true) {
    Show-Menu
    $option = Read-Host "Selecciona opcion (1-8)"
    
    switch ($option) {
        "1" { Transfer-Photos }
        "2" { Transfer-Documents }
        "3" { Upload-Files }
        "4" { Backup-Complete }
        "5" { 
            Write-Host ""
            Write-Host "BUSCAR ARCHIVOS EN EL CELULAR" -ForegroundColor Green
            Write-Host "------------------------------" -ForegroundColor DarkGray
            $search = Read-Host "Texto a buscar (ej: .jpg, vacaciones)"
            Write-Host "Buscando: $search" -ForegroundColor Yellow
            adb shell "find /sdcard -name '*${search}*' 2>/dev/null | head -20" 2>$null
            Write-Host ""
            Write-Host "Presiona Enter para continuar..."
            Read-Host
        }
        "6" {
            Write-Host ""
            Write-Host "ESPACIO EN ALMACENAMIENTO" -ForegroundColor Green
            Write-Host "-------------------------" -ForegroundColor DarkGray
            adb shell df -h /data /sdcard 2>$null
            Write-Host ""
            Write-Host "Presiona Enter para continuar..."
            Read-Host
        }
        "7" {
            Write-Host ""
            Write-Host "RECONECTAR" -ForegroundColor Green
            Write-Host "----------" -ForegroundColor DarkGray
            
            # Usar configuracion guardada
            $configFile = "$env:USERPROFILE\AndroidTools\android_config.txt"
            if (Test-Path $configFile) {
                $ip = $null
                Get-Content $configFile | ForEach-Object {
                    if ($_ -match 'ANDROID_IP="(.+)"') {
                        $ip = $matches[1]
                    }
                }
                
                if ($ip) {
                    Write-Host "Reconectando a $ip..." -ForegroundColor Yellow
                    adb connect "${ip}:5555" 2>$null
                    Start-Sleep -Seconds 2
                    
                    $devices = adb devices
                    if ($devices -match "${ip}:5555") {
                        Write-Host "OK: Reconectado" -ForegroundColor Green
                    } else {
                        Write-Host "ERROR: No se pudo reconectar" -ForegroundColor Red
                    }
                } else {
                    Write-Host "ERROR: No hay IP guardada" -ForegroundColor Red
                }
            } else {
                Write-Host "ERROR: No hay configuracion guardada" -ForegroundColor Red
                Write-Host "   Ejecuta primero: android_setup.ps1" -ForegroundColor Yellow
            }
            
            Write-Host ""
            Write-Host "Presiona Enter para continuar..."
            Read-Host
        }
        "8" {
            Write-Host ""
            Write-Host "Saliendo del programa..." -ForegroundColor Cyan
            exit 0
        }
        default {
            Write-Host "ERROR: Opcion invalida" -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
}