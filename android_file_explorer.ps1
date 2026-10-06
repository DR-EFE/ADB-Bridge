# android_file_explorer.ps1 - Explorador de archivos para Android (Sin emojis)
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
$script:AndroidDevice = Get-SingleReadyDevice
function adb {
    param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments)
    if ($Arguments.Count -eq 1 -and [string]$Arguments[0] -ceq 'devices') { return Get-AdbDevices }
    return Invoke-Adb -Serial $script:AndroidDevice.Serial -Arguments ([string[]]$Arguments)
}

function Show-FileExplorer {
    Clear-Host
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   ANDROID FILE EXPLORER                " -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Verificar conexion
    if ($null -eq $script:AndroidDevice) {
        Write-Host "ERROR: No hay dispositivo conectado" -ForegroundColor Red
        Write-Host "Ejecuta android_setup.ps1 primero" -ForegroundColor Yellow
        pause
        return $false
    }
    
    Write-Host "OK: Dispositivo conectado" -ForegroundColor Green
    Write-Host ""
    return $true
}

function Format-FileSize {
    param([long]$bytes)
    if ($bytes -ge 1GB) {
        return "{0:F2} GB" -f ($bytes / 1GB)
    } elseif ($bytes -ge 1MB) {
        return "{0:F2} MB" -f ($bytes / 1MB)
    } elseif ($bytes -ge 1KB) {
        return "{0:F2} KB" -f ($bytes / 1KB)
    } else {
        return "{0} B" -f $bytes
    }
}

function List-Directory {
    param([string]$path)
    
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   DIRECTORIO ACTUAL" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Ruta: $path" -ForegroundColor White
    Write-Host ""
    
    # Obtener informacion del directorio
    $items = adb shell "ls -la '$path'" 2>$null
    
    if (-not $items) {
        Write-Host "ERROR: No se puede acceder a esta carpeta" -ForegroundColor Red
        return $null
    }
    
    # Separar lineas
    $lines = $items -split "`r`n"
    
    Write-Host "CONTENIDO:" -ForegroundColor Cyan
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
    
    $count = 0
    $itemsList = @()
    
    foreach ($line in $lines) {
        # Saltar lineas vacias
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        
        # Formato: drwxrwx--- 10 root root 4096 2024-01-01 12:00 nombre
        if ($line -match "^(.)([rwx-]{9})\s+\d+\s+(\w+)\s+(\w+)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$") {
            $type = $matches[1]
            $permissions = $matches[2]
            $owner = $matches[3]
            $group = $matches[4]
            $size = [long]$matches[5]
            $date = $matches[6]
            $time = $matches[7]
            $name = $matches[8]
            
            $isDirectory = ($type -eq 'd')
            $sizeFormatted = Format-FileSize -bytes $size
            
            $count++
            $itemsList += [PSCustomObject]@{
                Number = $count
                Name = $name
                Type = $isDirectory
                Size = $sizeFormatted
                SizeBytes = $size
                Permissions = $permissions
                Owner = $owner
                Date = $date
                Time = $time
            }
            
            # Mostrar con colores
            if ($isDirectory) {
                Write-Host "  $($count.ToString().PadRight(4)) [DIR]  $name/" -ForegroundColor Green
            } else {
                Write-Host "  $($count.ToString().PadRight(4)) [FILE] $name" -ForegroundColor White
                Write-Host "         Size: $sizeFormatted" -ForegroundColor Gray
            }
        }
    }
    
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
    Write-Host "Total: $count elementos" -ForegroundColor Cyan
    
    return $itemsList
}

function Get-FriendlyPath {
    param([string]$path)
    
    # Reemplazar rutas comunes con nombres amigables
    $friendly = $path
    $friendly = $friendly -replace "^/sdcard", "[INTERNAL] Almacenamiento interno"
    $friendly = $friendly -replace "^/storage/emulated/0", "[INTERNAL] Almacenamiento interno"
    $friendly = $friendly -replace "^/storage", "[STORAGE] Almacenamiento"
    $friendly = $friendly -replace "^/data", "[DATA] Datos del sistema"
    $friendly = $friendly -replace "^/system", "[SYSTEM] Sistema"
    $friendly = $friendly -replace "^/cache", "[CACHE] Cache"
    
    return $friendly
}

function Show-QuickAccess {
    Write-Host ""
    Write-Host "ACCESO RAPIDO:" -ForegroundColor Cyan
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
    Write-Host "  a. /sdcard/ (Almacenamiento interno)" -ForegroundColor Green
    Write-Host "  b. /sdcard/DCIM/ (Fotos y videos)" -ForegroundColor Green
    Write-Host "  c. /sdcard/Download/ (Descargas)" -ForegroundColor Green
    Write-Host "  d. /sdcard/Documents/ (Documentos)" -ForegroundColor Green
    Write-Host "  e. /sdcard/Music/ (Musica)" -ForegroundColor Green
    Write-Host "  f. /sdcard/Movies/ (Videos)" -ForegroundColor Green
    Write-Host "  g. /sdcard/Pictures/ (Imagenes)" -ForegroundColor Green
    Write-Host "  h. /sdcard/WhatsApp/ (WhatsApp)" -ForegroundColor Green
    Write-Host "  i. /sdcard/Android/ (Datos de apps)" -ForegroundColor Green
    Write-Host "  j. /data/ (Datos sistema - limitado)" -ForegroundColor Yellow
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
}

function Show-Actions {
    Write-Host ""
    Write-Host "ACCIONES:" -ForegroundColor Cyan
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
    Write-Host "  [numero]  - Entrar a carpeta/abrir archivo" -ForegroundColor White
    Write-Host "  [..]      - Subir un nivel" -ForegroundColor White
    Write-Host "  [p]       - Subir al directorio padre" -ForegroundColor White
    Write-Host "  [d]       - Descargar archivo/carpeta seleccionada" -ForegroundColor White
    Write-Host "  [u]       - Subir archivo al directorio actual" -ForegroundColor White
    Write-Host "  [i]       - Informacion detallada" -ForegroundColor White
    Write-Host "  [s]       - Buscar archivos" -ForegroundColor White
    Write-Host "  [q]       - Acceso rapido" -ForegroundColor White
    Write-Host "  [h]       - Historial de carpetas" -ForegroundColor White
    Write-Host "  [x]       - Salir" -ForegroundColor White
    Write-Host "----------------------------------------" -ForegroundColor DarkGray
}

function Download-File {
    param([string]$remotePath, [string]$fileName)
    
    $downloadDir = "$env:USERPROFILE\Downloads\AndroidFiles"
    if (-not (Test-Path $downloadDir)) {
        New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
    }
    
    $localPath = "$downloadDir\$fileName"
    Write-Host ""
    Write-Host "Descargando: $fileName" -ForegroundColor Yellow
    Write-Host "Origen: $remotePath" -ForegroundColor Gray
    Write-Host "Destino: $localPath" -ForegroundColor Gray
    
    adb pull "$remotePath" "$localPath" 2>$null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK: Archivo descargado exitosamente!" -ForegroundColor Green
        
        # Obtener tamaño
        $fileInfo = Get-Item $localPath -ErrorAction SilentlyContinue
        if ($fileInfo) {
            Write-Host "Tamaño: $(Format-FileSize -bytes $fileInfo.Length)" -ForegroundColor Gray
        }
        
        $open = Read-Host "Abrir carpeta de destino? (s/n)"
        if ($open -eq 's') {
            Invoke-Item $downloadDir
        }
    } else {
        Write-Host "ERROR: No se pudo descargar" -ForegroundColor Red
    }
    
    Write-Host ""
    Read-Host "Presiona Enter para continuar"
}

function Download-Folder {
    param([string]$remotePath, [string]$folderName)
    
    $downloadDir = "$env:USERPROFILE\Downloads\AndroidFiles\$folderName"
    if (-not (Test-Path $downloadDir)) {
        New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
    }
    
    Write-Host ""
    Write-Host "Descargando carpeta: $folderName" -ForegroundColor Yellow
    Write-Host "Origen: $remotePath" -ForegroundColor Gray
    Write-Host "Destino: $downloadDir" -ForegroundColor Gray
    Write-Host "ADVERTENCIA: Esto puede tomar varios minutos" -ForegroundColor Yellow
    
    adb pull "$remotePath/" "$downloadDir/" 2>$null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK: Carpeta descargada exitosamente!" -ForegroundColor Green
        
        $open = Read-Host "Abrir carpeta de destino? (s/n)"
        if ($open -eq 's') {
            Invoke-Item $downloadDir
        }
    } else {
        Write-Host "ERROR: No se pudo descargar la carpeta" -ForegroundColor Red
    }
    
    Write-Host ""
    Read-Host "Presiona Enter para continuar"
}

function Upload-File {
    param([string]$remotePath)
    
    Write-Host ""
    Write-Host "SUBIR ARCHIVO" -ForegroundColor Green
    Write-Host "Directorio actual: $remotePath" -ForegroundColor Gray
    Write-Host ""
    
    # Mostrar archivos locales
    Write-Host "Archivos disponibles en PC (Descargas):" -ForegroundColor Cyan
    $localFiles = Get-ChildItem "$env:USERPROFILE\Downloads" -File | Select-Object -First 10
    $i = 1
    foreach ($file in $localFiles) {
        Write-Host "  $i. $($file.Name) ($(Format-FileSize -bytes $file.Length))" -ForegroundColor Gray
        $i++
    }
    Write-Host ""
    
    $uploadPath = Read-Host "Ruta del archivo a subir (ej: C:\Users\...\archivo.txt)"
    
    if (Test-Path $uploadPath) {
        $fileName = Split-Path $uploadPath -Leaf
        Write-Host "Subiendo: $fileName" -ForegroundColor Yellow
        Write-Host "Destino: $remotePath/$fileName" -ForegroundColor Gray
        
        adb push "$uploadPath" "$remotePath/" 2>$null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "OK: Archivo subido exitosamente!" -ForegroundColor Green
        } else {
            Write-Host "ERROR: No se pudo subir el archivo" -ForegroundColor Red
        }
    } else {
        Write-Host "ERROR: Archivo no encontrado" -ForegroundColor Red
    }
    
    Write-Host ""
    Read-Host "Presiona Enter para continuar"
}

function Search-Files {
    param([string]$currentPath)
    
    Write-Host ""
    Write-Host "BUSCAR ARCHIVOS" -ForegroundColor Green
    Write-Host "Directorio actual: $currentPath" -ForegroundColor Gray
    Write-Host ""
    
    $searchTerm = Read-Host "Nombre o extension a buscar (ej: .jpg, foto, *.mp4)"
    
    Write-Host ""
    Write-Host "Buscando: $searchTerm" -ForegroundColor Yellow
    Write-Host "Esto puede tomar unos segundos..." -ForegroundColor Gray
    Write-Host ""
    
    $results = adb shell "find '$currentPath' -name '*$searchTerm*' 2>/dev/null | head -30" 2>$null
    
    if ($results -and $results -ne "") {
        Write-Host "RESULTADOS:" -ForegroundColor Cyan
        Write-Host "----------------------------------------" -ForegroundColor DarkGray
        
        $resultsList = @()
        $count = 0
        foreach ($result in ($results -split "`r`n")) {
            if (-not [string]::IsNullOrWhiteSpace($result)) {
                $count++
                $resultsList += $result
                Write-Host "  $count. $result" -ForegroundColor White
            }
        }
        
        Write-Host "----------------------------------------" -ForegroundColor DarkGray
        Write-Host "Total encontrados: $count" -ForegroundColor Cyan
        Write-Host ""
        
        $option = Read-Host "Selecciona numero para navegar a esa ubicacion (Enter para salir)"
        if ($option -match '^\d+$' -and [int]$option -le $resultsList.Count) {
            $selectedPath = $resultsList[[int]$option - 1]
            # Verificar si es directorio o archivo
            $type = adb shell "[ -d '$selectedPath' ] && echo 'dir' || echo 'file'" 2>$null
            if ($type -match "dir") {
                return $selectedPath
            } else {
                $parentPath = Split-Path $selectedPath -Parent
                Write-Host "Abriendo carpeta contenedora: $parentPath" -ForegroundColor Yellow
                return $parentPath
            }
        }
    } else {
        Write-Host "No se encontraron resultados" -ForegroundColor Yellow
    }
    
    return $currentPath
}

function Show-FileInfo {
    param([string]$path, [PSObject]$item)
    
    Write-Host ""
    Write-Host "INFORMACION DEL ARCHIVO" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host "Nombre: $($item.Name)" -ForegroundColor White
    Write-Host "Ruta: $path" -ForegroundColor Gray
    Write-Host "Tipo: $(if ($item.Type) { 'Directorio' } else { 'Archivo' })" -ForegroundColor Gray
    Write-Host "Tamaño: $($item.Size)" -ForegroundColor Gray
    Write-Host "Permisos: $($item.Permissions)" -ForegroundColor Gray
    Write-Host "Propietario: $($item.Owner)" -ForegroundColor Gray
    Write-Host "Fecha: $($item.Date) $($item.Time)" -ForegroundColor Gray
    Write-Host "========================================" -ForegroundColor DarkGray
    
    Write-Host ""
    Read-Host "Presiona Enter para continuar"
}

# Programa principal
if (-not (Show-FileExplorer)) {
    exit
}

# Estado inicial
$history = @()
$currentPath = "/sdcard"
$history += $currentPath

while ($true) {
    Clear-Host
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   ANDROID FILE EXPLORER                " -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Ruta actual: $(Get-FriendlyPath -path $currentPath)" -ForegroundColor White
    Write-Host ""
    
    # Listar directorio
    $items = List-Directory -path $currentPath
    
    # Mostrar accesos rapidos
    Show-QuickAccess
    
    # Mostrar acciones
    Show-Actions
    
    Write-Host ""
    $option = Read-Host "Selecciona opcion"
    
    # Verificar si es un numero
    if ($option -match '^\d+$') {
        $num = [int]$option
        if ($items -and $num -le $items.Count) {
            $selected = $items[$num - 1]
            $newPath = "$currentPath/$($selected.Name)"
            
            if ($selected.Type) {
                # Es carpeta
                $currentPath = $newPath
                $history += $currentPath
            } else {
                # Es archivo - mostrar opciones
                Write-Host ""
                Write-Host "Archivo: $($selected.Name)" -ForegroundColor Cyan
                Write-Host "Tamaño: $($selected.Size)" -ForegroundColor Gray
                Write-Host ""
                Write-Host "Opciones: [d] Descargar  [i] Informacion  [Enter] Cancelar" -ForegroundColor Yellow
                $action = Read-Host "Selecciona"
                
                if ($action -eq 'd') {
                    Download-File -remotePath $newPath -fileName $selected.Name
                } elseif ($action -eq 'i') {
                    Show-FileInfo -path $newPath -item $selected
                }
            }
        } else {
            Write-Host "Opcion invalida" -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
    else {
        switch ($option.ToLower()) {
            ".." { 
                if ($currentPath -ne "/" -and $currentPath -ne "/sdcard") {
                    $currentPath = Split-Path $currentPath -Parent
                    $history += $currentPath
                }
            }
            "p" { 
                if ($currentPath -ne "/" -and $currentPath -ne "/sdcard") {
                    $currentPath = Split-Path $currentPath -Parent
                    $history += $currentPath
                }
            }
            "a" { $currentPath = "/sdcard"; $history += $currentPath }
            "b" { $currentPath = "/sdcard/DCIM"; $history += $currentPath }
            "c" { $currentPath = "/sdcard/Download"; $history += $currentPath }
            "d" { $currentPath = "/sdcard/Documents"; $history += $currentPath }
            "e" { $currentPath = "/sdcard/Music"; $history += $currentPath }
            "f" { $currentPath = "/sdcard/Movies"; $history += $currentPath }
            "g" { $currentPath = "/sdcard/Pictures"; $history += $currentPath }
            "h" { $currentPath = "/sdcard/WhatsApp"; $history += $currentPath }
            "i" { $currentPath = "/sdcard/Android"; $history += $currentPath }
            "j" { $currentPath = "/data"; $history += $currentPath }
            "q" { 
                Read-Host "Presiona Enter para continuar"
            }
            "s" { 
                $newPath = Search-Files -currentPath $currentPath
                if ($newPath -ne $currentPath) {
                    $currentPath = $newPath
                    $history += $currentPath
                }
            }
            "d" {
                Write-Host ""
                Write-Host "SELECCIONAR ELEMENTO PARA DESCARGAR" -ForegroundColor Yellow
                $num = Read-Host "Numero del elemento"
                if ($num -match '^\d+$') {
                    $numInt = [int]$num
                    if ($items -and $numInt -le $items.Count) {
                        $selected = $items[$numInt - 1]
                        $selectedPath = "$currentPath/$($selected.Name)"
                        
                        if ($selected.Type) {
                            Download-Folder -remotePath $selectedPath -folderName $selected.Name
                        } else {
                            Download-File -remotePath $selectedPath -fileName $selected.Name
                        }
                    }
                }
            }
            "u" {
                Upload-File -remotePath $currentPath
            }
            "h" {
                Write-Host ""
                Write-Host "HISTORIAL DE CARPETAS:" -ForegroundColor Cyan
                Write-Host "----------------------------------------" -ForegroundColor DarkGray
                $histCount = 1
                foreach ($item in $history) {
                    Write-Host "  $histCount. $item" -ForegroundColor Gray
                    $histCount++
                }
                Write-Host "----------------------------------------" -ForegroundColor DarkGray
                Write-Host ""
                $goto = Read-Host "Ir a numero de historial (Enter para salir)"
                if ($goto -match '^\d+$' -and [int]$goto -le $history.Count) {
                    $currentPath = $history[[int]$goto - 1]
                    # Recortar historial
                    $history = $history[0..([int]$goto - 1)]
                }
            }
            "x" { 
                Write-Host "Saliendo..." -ForegroundColor Cyan
                exit 0
            }
            default {
                Write-Host "Opcion no reconocida: $option" -ForegroundColor Red
                Start-Sleep -Seconds 1
            }
        }
    }
}
