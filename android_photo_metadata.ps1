# =====================================================================
# android_photo_metadata.ps1
# Modulo Forense de Extraccion, Normalizacion y Analisis Geoespacial
# de Metadatos Fotograficos (EXIF / GPS)
# Android Tools Forensic Framework
# =====================================================================

[CmdletBinding(DefaultParameterSetName = 'Interactive')]
param(
    # Modo Imagen Unica
    [Parameter(ParameterSetName = 'SingleImage', Position = 0)]
    [string]$ImagePath,

    # Modo Carpeta Local
    [Parameter(ParameterSetName = 'Folder')]
    [string]$FolderPath,

    # Modo Archivo Comprimido (.zip, .rar, .tar, .7z)
    [Parameter(ParameterSetName = 'Archive')]
    [string]$ArchivePath,

    # Modo Dispositivo Android (Escaneo y seleccion interactiva)
    [Parameter(ParameterSetName = 'Device')]
    [switch]$Device,

    # Reutilizar respaldo previo en Downloads\AndroidBackup
    [Parameter(ParameterSetName = 'Device')]
    [switch]$Reuse,

    # Forzar descarga fresca desde el dispositivo Android
    [Parameter(ParameterSetName = 'Device')]
    [switch]$Fresh,

    # Carpeta de salida personalizada
    [string]$OutputDir,

    # Descarga directa de ExifTool portátil a metadata\exiftool.exe
    [switch]$DownloadExifTool,

    # No abrir el navegador web al completar el informe
    [switch]$NoOpenBrowser,

    # No empaquetar el informe en archivo ZIP final
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing -ErrorAction SilentlyContinue

# Importar modulo ADB solo si existe
$AdbModulePath = Join-Path $PSScriptRoot 'AndroidAdb.psm1'
$script:HasAdbModule = Test-Path $AdbModulePath
if ($script:HasAdbModule) {
    try {
        Import-Module $AdbModulePath -Force -ErrorAction SilentlyContinue
    } catch {
        $script:HasAdbModule = $false
    }
}

# Extensiones de imagen soportadas
$script:SupportedExtensions = @('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.dng', '.tiff', '.tif', '.bmp')

# ---------------------------------------------------------------------
# LOGGING & CONSOLE UI
# ---------------------------------------------------------------------
function Write-ModuleLog {
    param(
        [ValidateSet('INFO', 'WARN', 'ERROR', 'SUCCESS', 'DEBUG')]
        [string]$Level = 'INFO',
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$LogFile
    )
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $color = switch ($Level) {
        'INFO'    { 'Cyan' }
        'WARN'    { 'Yellow' }
        'ERROR'   { 'Red' }
        'SUCCESS' { 'Green' }
        'DEBUG'   { 'DarkGray' }
    }
    $prefix = "[$ts] [$Level]"
    Write-Host "$prefix $Message" -ForegroundColor $color

    if ($LogFile) {
        try {
            $line = "$prefix $Message"
            [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
        } catch { }
    }
}

# ---------------------------------------------------------------------
# GESTOR DE EXIFTOOL PORTATIL
# ---------------------------------------------------------------------
function Find-ExifToolExecutable {
    $repoTool = Join-Path $PSScriptRoot 'metadata\exiftool.exe'
    if (Test-Path $repoTool) {
        return (Resolve-Path $repoTool).Path
    }
    $pathCmd = Get-Command 'exiftool' -ErrorAction SilentlyContinue
    if ($pathCmd) {
        return $pathCmd.Source
    }
    return $null
}

function Install-ExifToolPortable {
    param([string]$TargetDir)
    if (-not $TargetDir) {
        $TargetDir = Join-Path $PSScriptRoot 'metadata'
    }
    if (-not (Test-Path $TargetDir)) {
        New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    }

    Write-Host "`n=== Descarga Automatica de ExifTool Portatil ===" -ForegroundColor Cyan
    Write-Host "Destino: $TargetDir" -ForegroundColor Gray

    $url = 'https://exiftool.org/exiftool.zip'
    $fallbackUrl = 'https://github.com/exiftool/exiftool/archive/refs/tags/12.92.zip'
    $tempZip = Join-Path $env:TEMP ("exiftool_dl_" + [System.Guid]::NewGuid().ToString('N') + ".zip")
    $extractFolder = Join-Path $env:TEMP ("exiftool_ext_" + [System.Guid]::NewGuid().ToString('N'))

    try {
        Write-Host "Descargando desde $url..." -ForegroundColor Yellow
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
        Invoke-WebRequest -Uri $url -OutFile $tempZip -UseBasicParsing -TimeoutSec 45
    } catch {
        Write-Host "Aviso: No se pudo conectar a exiftool.org. Intentando mirror alternativo..." -ForegroundColor Yellow
        try {
            Invoke-WebRequest -Uri $fallbackUrl -OutFile $tempZip -UseBasicParsing -TimeoutSec 45
        } catch {
            Write-Host "ERROR: No se pudo descargar ExifTool. Se continuara con el extractor nativo .NET." -ForegroundColor Red
            return $null
        }
    }

    try {
        if (Test-Path $tempZip) {
            Write-Host "Descomprimiendo paquete..." -ForegroundColor Yellow
            Expand-Archive -LiteralPath $tempZip -DestinationPath $extractFolder -Force
            $exeFound = Get-ChildItem -Path $extractFolder -Recurse -Filter "exiftool*.exe" | Select-Object -First 1
            if ($exeFound) {
                $finalExe = Join-Path $TargetDir "exiftool.exe"
                Copy-Item -LiteralPath $exeFound.FullName -Destination $finalExe -Force
                Write-Host "ExifTool instalado correctamente en: $finalExe" -ForegroundColor Green
                try {
                    $ver = & $finalExe -ver
                    Write-Host "Version instalada: $ver" -ForegroundColor Green
                } catch { }
                return $finalExe
            } else {
                Write-Host "No se encontro un ejecutable en el paquete descargado." -ForegroundColor Yellow
            }
        }
    } catch {
        Write-Host "Error durante la extraccion de ExifTool: $($_.Exception.Message)" -ForegroundColor Red
    } finally {
        if (Test-Path $tempZip) { Remove-Item -LiteralPath $tempZip -Force -ErrorAction SilentlyContinue }
        if (Test-Path $extractFolder) { Remove-Item -LiteralPath $extractFolder -Recurse -Force -ErrorAction SilentlyContinue }
    }
    return $null
}

# ---------------------------------------------------------------------
# EXTRACCION DE ARCHIVOS COMPRIMIDOS (.ZIP, .RAR, .TAR, .7Z)
# Cumple el requisito explicito: crea un directorio aparte dedicado
# ---------------------------------------------------------------------
function Expand-ArchiveToStaging {
    param(
        [Parameter(Mandatory = $true)][string]$ArchiveFile,
        [Parameter(Mandatory = $true)][string]$StagingDirectory,
        [string]$LogFile
    )

    if (-not (Test-Path $ArchiveFile)) {
        throw "El archivo comprimido especificado no existe: $ArchiveFile"
    }

    if (-not (Test-Path $StagingDirectory)) {
        New-Item -ItemType Directory -Path $StagingDirectory -Force | Out-Null
    }

    $ext = [System.IO.Path]::GetExtension($ArchiveFile).ToLower()
    Write-ModuleLog -Level INFO -Message "Extrayendo archivo comprimido [$ext] a directorio dedicado: $StagingDirectory" -LogFile $LogFile

    $unpacked = $false

    # Metodo 1: ZIP nativo con Expand-Archive
    if ($ext -eq '.zip') {
        try {
            Expand-Archive -LiteralPath $ArchiveFile -DestinationPath $StagingDirectory -Force
            $unpacked = $true
            Write-ModuleLog -Level SUCCESS -Message "Extraccion ZIP completada con exito." -LogFile $LogFile
        } catch {
            Write-ModuleLog -Level WARN -Message "Expand-Archive fallo ($($_.Exception.Message)), intentando con herramientas del sistema..." -LogFile $LogFile
        }
    }

    # Metodo 2: UnRAR / WinRAR si es .rar o fallo zip
    if (-not $unpacked -and ($ext -eq '.rar' -or $ext -eq '.zip' -or $ext -eq '.7z')) {
        $unrarPaths = @(
            "C:\Program Files\WinRAR\UnRAR.exe",
            "C:\Program Files (x86)\WinRAR\UnRAR.exe",
            "C:\Program Files\WinRAR\WinRAR.exe",
            "C:\Program Files\7-Zip\7z.exe",
            "C:\Program Files (x86)\7-Zip\7z.exe"
        )
        foreach ($tool in $unrarPaths) {
            if (Test-Path $tool) {
                try {
                    Write-ModuleLog -Level INFO -Message "Extrayendo con herramienta detectada: $tool" -LogFile $LogFile
                    if ($tool -like "*7z.exe") {
                        & $tool x -y "-o$StagingDirectory" "$ArchiveFile" *>$null
                    } elseif ($tool -like "*UnRAR.exe") {
                        & $tool x -y -idq "$ArchiveFile" "$StagingDirectory\" *>$null
                    } else {
                        & $tool x -y -ibck "$ArchiveFile" "$StagingDirectory\" *>$null
                    }
                    $unpacked = $true
                    Write-ModuleLog -Level SUCCESS -Message "Extraccion completada con $tool." -LogFile $LogFile
                    break
                } catch {
                    Write-ModuleLog -Level WARN -Message "Error al extraer con ${tool}: $($_.Exception.Message)" -LogFile $LogFile
                }
            }
        }
    }

    # Metodo 3: Windows Built-in tar.exe (soporta tar, tgz, gz, bz2, y muchos zip)
    if (-not $unpacked) {
        $tarCmd = Get-Command 'tar.exe' -ErrorAction SilentlyContinue
        if ($tarCmd) {
            try {
                Write-ModuleLog -Level INFO -Message "Extrayendo con tar del sistema..." -LogFile $LogFile
                & $tarCmd.Source -xf "$ArchiveFile" -C "$StagingDirectory" *>$null
                $unpacked = $true
                Write-ModuleLog -Level SUCCESS -Message "Extraccion completada con tar.exe." -LogFile $LogFile
            } catch {
                Write-ModuleLog -Level WARN -Message "tar.exe fallo: $($_.Exception.Message)" -LogFile $LogFile
            }
        }
    }

    if (-not $unpacked) {
        throw "No se pudo extraer el archivo '$ArchiveFile'. Verifique que cuente con WinRAR, 7-Zip o tar.exe disponible."
    }

    # Localizar todas las imagenes extraidas
    $images = @(Get-ChildItem -Path $StagingDirectory -Recurse -File | Where-Object {
        $script:SupportedExtensions -contains $_.Extension.ToLower()
    })
    Write-ModuleLog -Level SUCCESS -Message "Imagenes encontradas en el archivo comprimido: $($images.Count)" -LogFile $LogFile
    return $images
}

# ---------------------------------------------------------------------
# PARSER NATIVO .NET DE METADATOS EXIF & GPS (FALLBACK PURO)
# ---------------------------------------------------------------------
function Convert-ExifRational {
    param([byte[]]$Bytes, [int]$Offset)
    if ($Offset + 8 -gt $Bytes.Length) { return 0.0 }
    $num = [BitConverter]::ToUInt32($Bytes, $Offset)
    $den = [BitConverter]::ToUInt32($Bytes, $Offset + 4)
    if ($den -eq 0) { return 0.0 }
    return [double]$num / [double]$den
}

function Get-ExifString {
    param([System.Drawing.Imaging.PropertyItem]$Item)
    if ($null -eq $Item -or $null -eq $Item.Value) { return '' }
    $str = [System.Text.Encoding]::ASCII.GetString($Item.Value)
    return $str.Trim([char]0, [char]32, [char]9, [char]10, [char]13)
}

function Get-ImageMetadataNative {
    param([Parameter(Mandatory = $true)][string]$FilePath)

    $result = [ordered]@{
        SourceFile       = $FilePath
        FileName         = [System.IO.Path]::GetFileName($FilePath)
        Make             = ''
        Model            = ''
        DateTimeOriginal = ''
        Software         = ''
        Orientation      = 1
        ImageWidth       = 0
        ImageHeight      = 0
        Lat              = $null
        Lon              = $null
        Altitude         = $null
        HasGPS           = $false
        ISO              = ''
        FNumber          = ''
        ExposureTime     = ''
        ExtractedWith    = 'DotNetNative'
    }

    try {
        $img = [System.Drawing.Image]::FromFile($FilePath)
        try {
            $result.ImageWidth = $img.Width
            $result.ImageHeight = $img.Height

            $props = @{}
            foreach ($prop in $img.PropertyItems) {
                $props[$prop.Id] = $prop
            }

            # 0x010F: Make
            if ($props.ContainsKey(0x010F)) { $result.Make = Get-ExifString $props[0x010F] }
            # 0x0110: Model
            if ($props.ContainsKey(0x0110)) { $result.Model = Get-ExifString $props[0x0110] }
            # 0x0131: Software
            if ($props.ContainsKey(0x0131)) { $result.Software = Get-ExifString $props[0x0131] }
            # 0x0112: Orientation
            if ($props.ContainsKey(0x0112)) {
                $val = $props[0x0112].Value
                if ($val.Length -ge 2) { $result.Orientation = [int][BitConverter]::ToUInt16($val, 0) }
            }
            # 0x9003: DateTimeOriginal (fallback 0x0132 DateTime, 0x9004 DateTimeDigitized)
            if ($props.ContainsKey(0x9003)) {
                $result.DateTimeOriginal = Get-ExifString $props[0x9003]
            } elseif ($props.ContainsKey(0x0132)) {
                $result.DateTimeOriginal = Get-ExifString $props[0x0132]
            } elseif ($props.ContainsKey(0x9004)) {
                $result.DateTimeOriginal = Get-ExifString $props[0x9004]
            }

            # 0x8827: ISO
            if ($props.ContainsKey(0x8827)) {
                $val = $props[0x8827].Value
                if ($val.Length -ge 2) { $result.ISO = [int][BitConverter]::ToUInt16($val, 0) }
            }
            # 0x829D: FNumber
            if ($props.ContainsKey(0x829D)) {
                $result.FNumber = [Math]::Round((Convert-ExifRational $props[0x829D].Value 0), 1)
            }
            # 0x829A: ExposureTime
            if ($props.ContainsKey(0x829A)) {
                $sec = Convert-ExifRational $props[0x829A].Value 0
                if ($sec -gt 0 -and $sec -lt 1) {
                    $inv = [int][Math]::Round(1.0 / $sec)
                    $result.ExposureTime = "1/$inv"
                } elseif ($sec -gt 0) {
                    $result.ExposureTime = "$sec"
                }
            }

            # GPS Tags:
            # 0x0001: GPSLatitudeRef, 0x0002: GPSLatitude
            # 0x0003: GPSLongitudeRef, 0x0004: GPSLongitude
            # 0x0006: GPSAltitude
            if ($props.ContainsKey(0x0001) -and $props.ContainsKey(0x0002) -and $props.ContainsKey(0x0003) -and $props.ContainsKey(0x0004)) {
                $latRef = Get-ExifString $props[0x0001]
                $latBytes = $props[0x0002].Value
                $latDeg = Convert-ExifRational $latBytes 0
                $latMin = Convert-ExifRational $latBytes 8
                $latSec = Convert-ExifRational $latBytes 16
                $latDec = $latDeg + ($latMin / 60.0) + ($latSec / 3600.0)
                if ($latRef -match 'S') { $latDec = -$latDec }

                $lonRef = Get-ExifString $props[0x0003]
                $lonBytes = $props[0x0004].Value
                $lonDeg = Convert-ExifRational $lonBytes 0
                $lonMin = Convert-ExifRational $lonBytes 8
                $lonSec = Convert-ExifRational $lonBytes 16
                $lonDec = $lonDeg + ($lonMin / 60.0) + ($lonSec / 3600.0)
                if ($lonRef -match 'W') { $lonDec = -$lonDec }

                if ($latDec -ge -90.0 -and $latDec -le 90.0 -and $lonDec -ge -180.0 -and $lonDec -le 180.0 -and ($latDec -ne 0.0 -or $lonDec -ne 0.0)) {
                    $result.Lat = [Math]::Round($latDec, 6)
                    $result.Lon = [Math]::Round($lonDec, 6)
                    $result.HasGPS = $true
                }

                if ($props.ContainsKey(0x0006)) {
                    $altVal = Convert-ExifRational $props[0x0006].Value 0
                    if ($props.ContainsKey(0x0005) -and $props[0x0005].Value[0] -eq 1) {
                        $altVal = -$altVal
                    }
                    $result.Altitude = [Math]::Round($altVal, 1)
                }
            }
        } finally {
            $img.Dispose()
        }
    } catch {
        # Error al leer con .NET (ej. archivo HEIC o imagen danada)
    }

    return [PSCustomObject]$result
}

# ---------------------------------------------------------------------
# GENERADOR DE MINIATURAS (THUMBNAILS)
# ---------------------------------------------------------------------
function New-ImageThumbnail {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$ThumbPath,
        [int]$MaxDim = 200
    )

    try {
        $src = [System.Drawing.Image]::FromFile($SourcePath)
        try {
            $w = $src.Width
            $h = $src.Height
            if ($w -le 0 -or $h -le 0) { return $false }
            $scale = [Math]::Min([double]$MaxDim / $w, [double]$MaxDim / $h)
            if ($scale -ge 1.0) {
                $newW = $w
                $newH = $h
            } else {
                $newW = [int]($w * $scale)
                $newH = [int]($h * $scale)
            }
            if ($newW -lt 1) { $newW = 1 }
            if ($newH -lt 1) { $newH = 1 }

            $thumb = New-Object System.Drawing.Bitmap $newW, $newH
            try {
                $g = [System.Drawing.Graphics]::FromImage($thumb)
                try {
                    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                    $g.DrawImage($src, 0, 0, $newW, $newH)
                } finally {
                    $g.Dispose()
                }

                $thumbDir = [System.IO.Path]::GetDirectoryName($ThumbPath)
                if (-not (Test-Path $thumbDir)) { New-Item -ItemType Directory -Path $thumbDir -Force | Out-Null }
                $thumb.Save($ThumbPath, [System.Drawing.Imaging.ImageFormat]::Jpeg)
                return $true
            } finally {
                $thumb.Dispose()
            }
        } finally {
            $src.Dispose()
        }
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------
# FASE 1: DESCUBRIMIENTO DE IMAGENES EN DISPOSITIVO ANDROID
# ---------------------------------------------------------------------
function Get-DeviceImageInventory {
    param(
        [Parameter(Mandatory = $true)]$AdbDevice,
        [string]$LogFile
    )

    $targetPaths = @(
        "/sdcard/DCIM",
        "/sdcard/Pictures",
        "/sdcard/Download",
        "/sdcard/Screenshots",
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
        "/sdcard/Android/media/org.telegram.messenger"
    )

    Write-ModuleLog -Level INFO -Message "Escaneando rutas multimedia en dispositivo Android ($($AdbDevice.Serial))..." -LogFile $LogFile
    $inventory = @()

    foreach ($p in $targetPaths) {
        try {
            $cmd = "find '$p' -maxdepth 4 -type f 2>/dev/null | grep -Ei '\.(jpg|jpeg|png|heic|heif|webp|dng|mp4|mov)$'"
            $files = @(Invoke-AdbCommand -Serial $AdbDevice.Serial -Arguments @('shell', $cmd))
            $cleanFiles = @($files | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and $_ -notmatch '(?i)permission denied|not found' })
            if ($cleanFiles.Count -gt 0) {
                $inventory += [PSCustomObject]@{
                    Path  = $p
                    Count = $cleanFiles.Count
                    Files = $cleanFiles
                }
                Write-ModuleLog -Level SUCCESS -Message "Encontrada carpeta: $p ($($cleanFiles.Count) elementos)" -LogFile $LogFile
            }
        } catch { }
    }

    return $inventory
}

# ---------------------------------------------------------------------
# FASE 2: PULL DESDE ANDROID
# ---------------------------------------------------------------------
function Invoke-AdbPullImages {
    param(
        [Parameter(Mandatory = $true)]$AdbDevice,
        [Parameter(Mandatory = $true)][string[]]$DevicePaths,
        [Parameter(Mandatory = $true)][string]$DestinationFolder,
        [string]$LogFile
    )

    if (-not (Test-Path $DestinationFolder)) {
        New-Item -ItemType Directory -Path $DestinationFolder -Force | Out-Null
    }

    Write-ModuleLog -Level INFO -Message "Iniciando descarga de imagenes desde el dispositivo..." -LogFile $LogFile
    foreach ($devPath in $DevicePaths) {
        $folderName = [System.IO.Path]::GetFileName($devPath.TrimEnd('/'))
        $destSub = Join-Path $DestinationFolder $folderName
        New-Item -ItemType Directory -Path $destSub -Force | Out-Null

        Write-ModuleLog -Level INFO -Message "Descargando [$devPath] -> [$destSub]..." -LogFile $LogFile
        try {
            Invoke-AdbCommand -Serial $AdbDevice.Serial -Arguments @('pull', '--sync', $devPath, $destSub)
            Write-ModuleLog -Level SUCCESS -Message "Descarga completada para $devPath" -LogFile $LogFile
        } catch {
            Write-ModuleLog -Level WARN -Message "Fallo con --sync, reintentando pull estandar: $($_.Exception.Message)" -LogFile $LogFile
            try {
                Invoke-AdbCommand -Serial $AdbDevice.Serial -Arguments @('pull', $devPath, $destSub)
                Write-ModuleLog -Level SUCCESS -Message "Descarga estandar completada para $devPath" -LogFile $LogFile
            } catch {
                Write-ModuleLog -Level ERROR -Message "Error al descargar ${devPath}: $($_.Exception.Message)" -LogFile $LogFile
            }
        }
    }
}

# ---------------------------------------------------------------------
# FASE 3: EXTRACCION DE METADATOS EXIF
# ---------------------------------------------------------------------
function Invoke-ExifExtraction {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDirectory,
        [Parameter(Mandatory = $true)][string]$RawCsvPath,
        [string]$LogFile
    )

    $exiftool = Find-ExifToolExecutable
    $extractedItems = @()

    if ($exiftool) {
        Write-ModuleLog -Level INFO -Message "Ejecutando ExifTool: $exiftool" -LogFile $LogFile
        try {
            # -n: valores numericos decimales puros (GPS Lat/Lon decimal)
            # -r: recursivo
            # -csv: salida estructurada en tabla CSV
            # -charset filename=utf8
            & $exiftool -csv -r -n -charset filename=utf8 "$SourceDirectory" | Out-File -FilePath $RawCsvPath -Encoding utf8
            Write-ModuleLog -Level SUCCESS -Message "ExifTool finalizo con exito. Guardado en: $RawCsvPath" -LogFile $LogFile
            
            if (Test-Path $RawCsvPath) {
                $csvRows = Import-Csv -Path $RawCsvPath
                return $csvRows
            }
        } catch {
            Write-ModuleLog -Level WARN -Message "ExifTool retorno error ($($_.Exception.Message)). Usando extractor nativo..." -LogFile $LogFile
        }
    } else {
        Write-ModuleLog -Level INFO -Message "ExifTool no detectado en PATH ni en metadata\. Usando extractor forense nativo .NET..." -LogFile $LogFile
    }

    # Fallback Nativo .NET
    $files = Get-ChildItem -Path $SourceDirectory -Recurse -File | Where-Object {
        $script:SupportedExtensions -contains $_.Extension.ToLower()
    }

    $total = $files.Count
    $current = 0
    Write-ModuleLog -Level INFO -Message "Extrayendo metadatos nativos de $total imagenes..." -LogFile $LogFile

    $rows = @()
    foreach ($file in $files) {
        $current++
        if ($current % 50 -eq 0 -or $current -eq $total) {
            Write-Progress -Activity "Extraccion de Metadatos Forenses" -Status "Procesando $current de $total" -PercentComplete (($current / $total) * 100)
        }
        $meta = Get-ImageMetadataNative -FilePath $file.FullName
        $rows += $meta
    }
    Write-Progress -Activity "Extraccion de Metadatos Forenses" -Completed

    # Exportar a CSV crudo
    $rows | Export-Csv -Path $RawCsvPath -NoTypeInformation -Encoding utf8
    Write-ModuleLog -Level SUCCESS -Message "Extraccion nativa completada ($($rows.Count) procesadas). CSV generado en: $RawCsvPath" -LogFile $LogFile
    return $rows
}

# ---------------------------------------------------------------------
# FASE 4: NORMALIZACION
# ---------------------------------------------------------------------
function Convert-MetadataNormalized {
    param(
        [Parameter(Mandatory = $true)][object[]]$RawMetadata,
        [Parameter(Mandatory = $true)][string]$SourceDirectory,
        [Parameter(Mandatory = $true)][string]$ThumbsDirectory,
        [Parameter(Mandatory = $true)][string]$NormalizedJsonPath,
        [Parameter(Mandatory = $true)][string]$NormalizedCsvPath,
        [string]$LogFile
    )

    Write-ModuleLog -Level INFO -Message "Normalizando $($RawMetadata.Count) registros de metadatos..." -LogFile $LogFile
    $normalizedList = @()
    $idx = 0

    foreach ($item in $RawMetadata) {
        $idx++
        $srcPath = if ($item.SourceFile) { $item.SourceFile } elseif ($item.File) { $item.File } else { '' }
        $fullPath = if ([System.IO.Path]::IsPathRooted($srcPath)) { $srcPath } else { Join-Path $SourceDirectory $srcPath }
        $fileName = [System.IO.Path]::GetFileName($fullPath)

        # Ruta relativa limpia
        $relPath = if (Test-Path $fullPath) {
            $fullPath.Substring($SourceDirectory.Length).TrimStart('\', '/')
        } else {
            $fileName
        }

        # Extraccion y normalizacion de coordenadas
        $lat = $null
        $lon = $null
        $alt = $null
        $hasGps = $false

        $rawLat = if ($item.GPSLatitude) { $item.GPSLatitude } elseif ($item.Lat) { $item.Lat } else { $null }
        $rawLon = if ($item.GPSLongitude) { $item.GPSLongitude } elseif ($item.Lon) { $item.Lon } else { $null }
        $rawAlt = if ($item.GPSAltitude) { $item.GPSAltitude } elseif ($item.Altitude) { $item.Altitude } else { $null }

        $parsedLat = 0.0
        $parsedLon = 0.0
        if ($rawLat -and [double]::TryParse([string]$rawLat, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsedLat)) {
            if ($rawLon -and [double]::TryParse([string]$rawLon, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsedLon)) {
                if ($parsedLat -ge -90.0 -and $parsedLat -le 90.0 -and $parsedLon -ge -180.0 -and $parsedLon -le 180.0 -and ($parsedLat -ne 0.0 -or $parsedLon -ne 0.0)) {
                    $lat = [Math]::Round($parsedLat, 6)
                    $lon = [Math]::Round($parsedLon, 6)
                    $hasGps = $true
                }
            }
        }

        if ($rawAlt) {
            $parsedAlt = 0.0
            if ([double]::TryParse([string]$rawAlt, [System.Globalization.NumberStyles]::Any, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsedAlt)) {
                $alt = [Math]::Round($parsedAlt, 1)
            }
        }

        # Fecha original (fallbacks comunes en camaras de smartphones)
        $dateOrig = if ($item.DateTimeOriginal) { $item.DateTimeOriginal }
                    elseif ($item.CreateDate) { $item.CreateDate }
                    elseif ($item.ModifyDate) { $item.ModifyDate }
                    elseif ($item.DateTime) { $item.DateTime }
                    else { '' }

        # Limpiar texto de fecha a formato consistente
        $dateClean = [string]$dateOrig
        if ($dateClean.Length -ge 19) {
            $dateClean = $dateClean.Substring(0, 19)
        }

        # Dispositivo
        $make = if ($item.Make) { [string]$item.Make } else { '' }
        $model = if ($item.Model) { [string]$item.Model } else { '' }

        # Orientacion
        $orient = 1
        if ($item.Orientation) {
            $parsedOrient = 1
            if ([int]::TryParse([string]$item.Orientation, [ref]$parsedOrient)) {
                $orient = $parsedOrient
            }
        }

        # Software / Editor
        $software = if ($item.Software) { [string]$item.Software } else { '' }

        # Parametros fotograficos
        $iso = if ($item.ISO) { [string]$item.ISO } else { '' }
        $fnum = if ($item.FNumber) { [string]$item.FNumber } else { '' }
        $exp = if ($item.ExposureTime) { [string]$item.ExposureTime } else { '' }
        $w = if ($item.ImageWidth) { [int]$item.ImageWidth } else { 0 }
        $h = if ($item.ImageHeight) { [int]$item.ImageHeight } else { 0 }

        # Miniatura para el visor web
        $thumbRel = ''
        if (Test-Path $fullPath) {
            $thumbFileName = "thumb_${idx}_" + [System.IO.Path]::GetFileNameWithoutExtension($fileName) + ".jpg"
            $thumbFullPath = Join-Path $ThumbsDirectory $thumbFileName
            if (New-ImageThumbnail -SourcePath $fullPath -ThumbPath $thumbFullPath -MaxDim 220) {
                $thumbRel = "thumbs/$thumbFileName"
            }
        }

        # Hash SHA-256 del archivo
        $sha = ''
        $size = 0
        if (Test-Path $fullPath) {
            try {
                $itemInfo = Get-Item -LiteralPath $fullPath
                $size = $itemInfo.Length
                $sha = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash
            } catch { }
        }

        $gMapsUrl = if ($hasGps) { "https://www.google.com/maps/search/?api=1&query=$lat,$lon" } else { '' }

        $normItem = [ordered]@{
            File             = $relPath.Replace('\', '/')
            FileName         = $fileName
            SourceFile       = $fullPath
            DateTimeOriginal = $dateClean
            Make             = $make.Trim()
            Model            = $model.Trim()
            Lat              = $lat
            Lon              = $lon
            Altitude         = $alt
            HasGPS           = $hasGps
            Orientation      = $orient
            Software         = $software.Trim()
            ISO              = $iso
            FNumber          = $fnum
            ExposureTime     = $exp
            ImageWidth       = $w
            ImageHeight      = $h
            Thumbnail        = $thumbRel
            ThumbRel         = $thumbRel
            GoogleMapsUrl    = $gMapsUrl
            Sha256           = $sha
            FileSizeBytes    = $size
        }

        $normalizedList += [PSCustomObject]$normItem
    }

    # Guardar JSON y CSV (asegurar siempre formato array JSON incluso para 1 elemento)
    $normalizedJson = if ($normalizedList.Count -eq 0) {
        "[]"
    } elseif ($normalizedList.Count -eq 1) {
        "[" + ($normalizedList[0] | ConvertTo-Json -Depth 6) + "]"
    } else {
        $normalizedList | ConvertTo-Json -Depth 6
    }
    [System.IO.File]::WriteAllText($NormalizedJsonPath, $normalizedJson, [System.Text.Encoding]::UTF8)

    $normalizedList | Export-Csv -Path $NormalizedCsvPath -NoTypeInformation -Encoding utf8
    Write-ModuleLog -Level SUCCESS -Message "Normalizacion guardada en: $NormalizedJsonPath" -LogFile $LogFile

    return $normalizedList
}

# ---------------------------------------------------------------------
# FASE 5: ANALISIS AGREGADO FORENSE
# ---------------------------------------------------------------------
function Get-MetadataAnalysis {
    param(
        [Parameter(Mandatory = $true)][object[]]$NormalizedItems,
        [Parameter(Mandatory = $true)][string]$AnalysisJsonPath,
        [string]$LogFile
    )

    Write-ModuleLog -Level INFO -Message "Generando estadisticas forenses y clusters geoespaciales..." -LogFile $LogFile

    $total = $NormalizedItems.Count
    $withGps = @($NormalizedItems | Where-Object { $_.HasGPS -eq $true })
    $withoutGps = $total - $withGps.Count
    $gpsCoveragePct = if ($total -gt 0) { [Math]::Round(($withGps.Count / $total) * 100, 2) } else { 0.0 }

    # Rango temporal
    $datesWithVal = @($NormalizedItems | Where-Object { -not [string]::IsNullOrWhiteSpace($_.DateTimeOriginal) } | Select-Object -ExpandProperty DateTimeOriginal | Sort-Object)
    $minDate = if ($datesWithVal.Count -gt 0) { $datesWithVal[0] } else { 'N/A' }
    $maxDate = if ($datesWithVal.Count -gt 0) { $datesWithVal[$datesWithVal.Count - 1] } else { 'N/A' }

    # Dispositivos
    $devicesGroup = $NormalizedItems | Group-Object {
        $d = ("$($_.Make) $($_.Model)").Trim()
        if ([string]::IsNullOrWhiteSpace($d)) { 'Desconocido / Sin EXIF' } else { $d }
    } | Sort-Object Count -Descending | ForEach-Object {
        [PSCustomObject]@{ Device = $_.Name; Count = $_.Count }
    }

    # Top Clusters Geograficos (Redondeo a 3 decimales ≈ 110 metros)
    $clusters = @()
    if ($withGps.Count -gt 0) {
        $clusters = $withGps | Group-Object {
            "{0:F3},{1:F3}" -f $_.Lat, $_.Lon
        } | Sort-Object Count -Descending | Select-Object -First 25 | ForEach-Object {
            $parts = $_.Name.Split(',')
            $cLat = [double]::Parse($parts[0], [System.Globalization.CultureInfo]::InvariantCulture)
            $cLon = [double]::Parse($parts[1], [System.Globalization.CultureInfo]::InvariantCulture)
            [PSCustomObject]@{
                Lat           = $cLat
                Lon           = $cLon
                Count         = $_.Count
                GoogleMapsUrl = "https://www.google.com/maps/search/?api=1&query=$cLat,$cLon"
            }
        }
    }

    # Orientaciones
    $orientGroup = $NormalizedItems | Group-Object Orientation | ForEach-Object {
        [PSCustomObject]@{ Orientation = $_.Name; Count = $_.Count }
    }

    # Software / Edicion
    $edited = @($NormalizedItems | Where-Object { -not [string]::IsNullOrWhiteSpace($_.Software) })
    $softwareGroup = $edited | Group-Object Software | Sort-Object Count -Descending | ForEach-Object {
        [PSCustomObject]@{ Software = $_.Name; Count = $_.Count }
    }

    # Sin ningun metadato EXIF identificable
    $stripped = @($NormalizedItems | Where-Object { [string]::IsNullOrWhiteSpace($_.Make) -and [string]::IsNullOrWhiteSpace($_.DateTimeOriginal) -and -not $_.HasGPS })

    $analysis = [ordered]@{
        TotalImages        = $total
        GpsImages          = $withGps.Count
        NoGpsImages        = $withoutGps
        GpsCoveragePercent = $gpsCoveragePct
        DateRange          = [ordered]@{ Min = $minDate; Max = $maxDate }
        Devices            = $devicesGroup
        TopClusters        = $clusters
        Orientations       = $orientGroup
        SoftwareDetected   = $softwareGroup
        EditedCount        = $edited.Count
        StrippedCount      = $stripped.Count
        AnalyzedAt         = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    }

    $analysisJson = $analysis | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($AnalysisJsonPath, $analysisJson, [System.Text.Encoding]::UTF8)
    Write-ModuleLog -Level SUCCESS -Message "Resumen de analisis guardado en: $AnalysisJsonPath" -LogFile $LogFile

    return $analysis
}

# ---------------------------------------------------------------------
# FASE 6: GENERACION DE MAPA INTERACTIVO Y DASHBOARD HTML
# ---------------------------------------------------------------------
function New-ForensicHtmlOutputs {
    param(
        [Parameter(Mandatory = $true)][object[]]$NormalizedItems,
        [Parameter(Mandatory = $true)]$AnalysisSummary,
        [Parameter(Mandatory = $true)]$ManifestData,
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [string]$LogFile
    )

    $templateMap = Join-Path $PSScriptRoot 'metadata\templates\map.html'
    $templateDash = Join-Path $PSScriptRoot 'metadata\templates\dashboard.html'

    if (-not (Test-Path $templateMap) -or -not (Test-Path $templateDash)) {
        Write-ModuleLog -Level WARN -Message "No se encontraron las plantillas HTML en metadata\templates\. Se omitira la generacion visual." -LogFile $LogFile
        return
    }

    $mapHtmlPath = Join-Path $OutputDirectory 'map.html'
    $dashHtmlPath = Join-Path $OutputDirectory 'dashboard.html'

    Write-ModuleLog -Level INFO -Message "Compilando mapa interactivo y dashboard forense..." -LogFile $LogFile

    $photosJson = if ($NormalizedItems.Count -eq 0) {
        "[]"
    } elseif ($NormalizedItems.Count -eq 1) {
        "[" + ($NormalizedItems[0] | ConvertTo-Json -Depth 6 -Compress) + "]"
    } else {
        $NormalizedItems | ConvertTo-Json -Depth 6 -Compress
    }
    $summaryJson = $AnalysisSummary | ConvertTo-Json -Depth 6 -Compress
    $manifestJson = $ManifestData | ConvertTo-Json -Depth 6 -Compress

    # Compilar map.html
    $mapContent = [System.IO.File]::ReadAllText($templateMap, [System.Text.Encoding]::UTF8)
    $mapContent = $mapContent.Replace('/*__PHOTOS_JSON__*/', $photosJson)
    [System.IO.File]::WriteAllText($mapHtmlPath, $mapContent, [System.Text.Encoding]::UTF8)
    Write-ModuleLog -Level SUCCESS -Message "Mapa generado: $mapHtmlPath" -LogFile $LogFile

    # Compilar dashboard.html
    $dashContent = [System.IO.File]::ReadAllText($templateDash, [System.Text.Encoding]::UTF8)
    $dashContent = $dashContent.Replace('/*__PHOTOS_JSON__*/', $photosJson)
    $dashContent = $dashContent.Replace('/*__SUMMARY_JSON__*/', $summaryJson)
    $dashContent = $dashContent.Replace('/*__MANIFEST_JSON__*/', $manifestJson)
    [System.IO.File]::WriteAllText($dashHtmlPath, $dashContent, [System.Text.Encoding]::UTF8)
    Write-ModuleLog -Level SUCCESS -Message "Dashboard generado: $dashHtmlPath" -LogFile $LogFile

    return @{ Map = $mapHtmlPath; Dashboard = $dashHtmlPath }
}

# ---------------------------------------------------------------------
# FASE 7: CADENA DE CUSTODIA Y EVIDENCIA
# ---------------------------------------------------------------------
function Save-MetadataEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [Parameter(Mandatory = $true)]$ManifestData,
        [string]$LogFile
    )

    Write-ModuleLog -Level INFO -Message "Calculando hashes SHA-256 para cadena de custodia..." -LogFile $LogFile
    $filesToHash = @(
        'raw_metadata.csv',
        'normalized_metadata.json',
        'normalized_metadata.csv',
        'analysis_summary.json',
        'map.html',
        'dashboard.html'
    )

    $artifacts = @()
    foreach ($fn in $filesToHash) {
        $p = Join-Path $OutputDirectory $fn
        if (Test-Path $p) {
            $item = Get-Item -LiteralPath $p
            $hash = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
            $artifacts += [ordered]@{
                FileName  = $fn
                SizeBytes = $item.Length
                Sha256    = $hash
            }
        }
    }

    $ManifestData.Artifacts = $artifacts
    $manifestPath = Join-Path $OutputDirectory 'evidence_manifest.json'
    $manifestJson = $ManifestData | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($manifestPath, $manifestJson, [System.Text.Encoding]::UTF8)
    Write-ModuleLog -Level SUCCESS -Message "Manifiesto de evidencia sellado en: $manifestPath" -LogFile $LogFile

    # Crear informe legible README.txt
    $readmePath = Join-Path $OutputDirectory 'README.txt'
    $readmeLines = @(
        "=====================================================================",
        " INFORME FORENSE DE METADATOS Y GEOLOCALIZACION FOTOGRAFICA",
        " Android Tools Forensic Module",
        "=====================================================================",
        "Sesion ID        : $($ManifestData.SessionId)",
        "Fecha Adquisicion: $($ManifestData.AcquisitionTime)",
        "Equipo Operador  : $($ManifestData.HostName) ($($ManifestData.User))",
        "Dispositivo ADB  : $($ManifestData.DeviceSerial)",
        "Tipo de Fuente   : $($ManifestData.SourceType)",
        "Origen Datos     : $($ManifestData.SourceOrigin)",
        "Total Imagenes   : $($ManifestData.TotalImages)",
        "",
        "--- HASHES DE EVIDENCIA (SHA-256) ---"
    )
    foreach ($a in $artifacts) {
        $readmeLines += ("{0,-28} | {1,8} KB | {2}" -f $a.FileName, [Math]::Round($a.SizeBytes / 1024, 1), $a.Sha256)
    }
    $readmeLines += @(
        "",
        "--- VISORES GENERADOS ---",
        "Dashboard Completo : file:///" + (Join-Path $OutputDirectory 'dashboard.html').Replace('\', '/'),
        "Mapa Interactivo   : file:///" + (Join-Path $OutputDirectory 'map.html').Replace('\', '/'),
        "====================================================================="
    )
    [System.IO.File]::WriteAllLines($readmePath, $readmeLines, [System.Text.Encoding]::UTF8)

    # Opcional: Proteger directorio con ACL forense si AndroidAdb lo soporta
    if ($script:HasAdbModule -and (Get-Command Protect-BackupDirectory -ErrorAction SilentlyContinue)) {
        try { Protect-BackupDirectory -Path $OutputDirectory | Out-Null } catch { }
    }

    return $manifestPath
}

# ---------------------------------------------------------------------
# ORQUESTADOR PRINCIPAL
# ---------------------------------------------------------------------
function Start-MetadataExtractionFlow {
    param(
        [string]$SingleFile,
        [string]$InputFolder,
        [string]$ArchiveFile,
        [switch]$AdbMode,
        [switch]$AdbReuse,
        [switch]$AdbFresh,
        [string]$CustomOut,
        [switch]$SkipBrowser,
        [switch]$SkipZipPackaging
    )

    $sessionTs = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $defaultBase = Join-Path ([Environment]::GetFolderPath('UserProfile')) "Downloads\AndroidBackup"
    if (-not (Test-Path $defaultBase)) {
        New-Item -ItemType Directory -Path $defaultBase -Force | Out-Null
    }

    $workdir = if ($CustomOut) { $CustomOut } else { Join-Path $defaultBase "Metadata_$sessionTs" }
    New-Item -ItemType Directory -Path $workdir -Force | Out-Null

    $logFile = Join-Path $workdir 'module.log'
    $sourceDir = Join-Path $workdir 'source'
    $thumbsDir = Join-Path $workdir 'thumbs'
    $archiveExtDir = Join-Path $workdir 'archive_extracted'
    New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null
    New-Item -ItemType Directory -Path $thumbsDir -Force | Out-Null

    Write-ModuleLog -Level INFO -Message "Iniciando sesion de extraccion de metadatos: Metadata_$sessionTs" -LogFile $logFile
    Write-ModuleLog -Level INFO -Message "Directorio de trabajo: $workdir" -LogFile $logFile

    $manifest = [ordered]@{
        SessionId       = "Metadata_$sessionTs"
        AcquisitionTime = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        HostName        = $env:COMPUTERNAME
        User            = $env:USERNAME
        DeviceSerial    = 'N/A'
        SourceType      = 'Unknown'
        SourceOrigin    = ''
        TotalImages     = 0
        Artifacts       = @()
    }

    # =================================================================
    # ETAPA 1 Y 2: ADQUISICION DE ARCHIVOS SEGUN MODO
    # =================================================================

    # 1. Modo Archivo Comprimido (.zip, .rar, .tar, .7z)
    if ($ArchiveFile) {
        $manifest.SourceType = 'Archive'
        $manifest.SourceOrigin = $ArchiveFile
        Write-ModuleLog -Level INFO -Message "Modo Archivo Comprimido detectado: $ArchiveFile" -LogFile $logFile
        
        # Descomprimir en directorio dedicado obligatorio
        $extractedImages = Expand-ArchiveToStaging -ArchiveFile $ArchiveFile -StagingDirectory $archiveExtDir -LogFile $logFile
        
        Write-ModuleLog -Level INFO -Message "Copiando imagenes descomprimidas al directorio de trabajo source\..." -LogFile $logFile
        $copyCount = 0
        foreach ($img in $extractedImages) {
            $copyCount++
            $destName = ("img_{0:D4}_" -f $copyCount) + $img.Name
            Copy-Item -LiteralPath $img.FullName -Destination (Join-Path $sourceDir $destName) -Force
        }
    }
    # 2. Modo Imagen Unica
    elseif ($SingleFile) {
        $manifest.SourceType = 'SingleImage'
        $manifest.SourceOrigin = $SingleFile
        Write-ModuleLog -Level INFO -Message "Modo Imagen Unica: $SingleFile" -LogFile $logFile
        if (-not (Test-Path $SingleFile)) { throw "El archivo especificado no existe: $SingleFile" }
        Copy-Item -LiteralPath $SingleFile -Destination (Join-Path $sourceDir ([System.IO.Path]::GetFileName($SingleFile))) -Force
    }
    # 3. Modo Carpeta Local
    elseif ($InputFolder) {
        $manifest.SourceType = 'Folder'
        $manifest.SourceOrigin = $InputFolder
        Write-ModuleLog -Level INFO -Message "Modo Carpeta Local: $InputFolder" -LogFile $logFile
        if (-not (Test-Path $InputFolder)) { throw "La carpeta especificada no existe: $InputFolder" }
        
        $localFiles = Get-ChildItem -Path $InputFolder -Recurse -File | Where-Object {
            $script:SupportedExtensions -contains $_.Extension.ToLower()
        }
        Write-ModuleLog -Level INFO -Message "Copiando $($localFiles.Count) imagenes al entorno de analisis..." -LogFile $logFile
        $cnt = 0
        foreach ($lf in $localFiles) {
            $cnt++
            $dest = Join-Path $sourceDir ("local_{0:D4}_" -f $cnt + $lf.Name)
            Copy-Item -LiteralPath $lf.FullName -Destination $dest -Force
        }
    }
    # 4. Modo Dispositivo Android (ADB)
    elseif ($AdbMode) {
        $manifest.SourceType = 'AndroidDevice'
        if (-not $script:HasAdbModule) {
            throw "El modulo AndroidAdb.psm1 no esta disponible en el entorno."
        }

        # Reconexion y seleccion de dispositivo
        $adbDevice = Select-CurrentAdbDevice -Persist
        if (-not (Test-AdbDeviceReady -Device $adbDevice -AutoReconnect $true)) {
            throw "El dispositivo seleccionado ($($adbDevice.Serial)) no esta listo tras el intento de conexion."
        }
        $manifest.DeviceSerial = $adbDevice.Serial
        $manifest.SourceOrigin = "Android ADB Serial: $($adbDevice.Serial)"
        Write-ModuleLog -Level SUCCESS -Message "Dispositivo ADB conectado: $($adbDevice.Serial)" -LogFile $logFile

        # Verificar si hay respaldo previo para reusar
        $reused = $false
        if ($AdbReuse -or (-not $AdbFresh)) {
            $recentBackups = @(Get-ChildItem -Path $defaultBase -Directory | Where-Object { $_.Name -like "*$($adbDevice.Serial)*" -or $_.Name -like "DCIM*" } | Sort-Object LastWriteTime -Descending)
            if ($recentBackups.Count -gt 0 -and ($AdbReuse -or ($Host.UI.RawUI -and (Read-Host "Se detecto un respaldo previo en '$($recentBackups[0].FullName)'. ¿Desea reusarlo para ahorrar tiempo? (S/n)") -ne 'n'))) {
                Write-ModuleLog -Level INFO -Message "Reutilizando fotos locales de: $($recentBackups[0].FullName)" -LogFile $logFile
                $reusedFiles = Get-ChildItem -Path $recentBackups[0].FullName -Recurse -File | Where-Object { $script:SupportedExtensions -contains $_.Extension.ToLower() }
                $rcnt = 0
                foreach ($rf in $reusedFiles) {
                    $rcnt++
                    Copy-Item -LiteralPath $rf.FullName -Destination (Join-Path $sourceDir ("prev_{0:D4}_" -f $rcnt + $rf.Name)) -Force
                }
                $reused = $true
            }
        }

        # Si no se reutilizo, hacer escaneo y pull fresco
        if (-not $reused) {
            $inv = Get-DeviceImageInventory -AdbDevice $adbDevice -LogFile $logFile
            if ($inv.Count -eq 0) {
                throw "No se encontraron carpetas con imagenes en las rutas comunes del dispositivo."
            }
            
            Write-Host "`nCarpetas multimedia encontradas en el dispositivo:" -ForegroundColor Cyan
            for ($i = 0; $i -lt $inv.Count; $i++) {
                Write-Host (" [{0}] {1} ({2} imagenes)" -f ($i + 1), $inv[$i].Path, $inv[$i].Count) -ForegroundColor Green
            }
            Write-Host (" [A] Descargar TODAS las carpetas ({0} total)" -f (($inv | Measure-Object -Property Count -Sum).Sum)) -ForegroundColor Yellow

            $sel = Read-Host "`nSeleccione las carpetas a descargar (ej: 1,2 o A)"
            $selectedPaths = @()
            if ($sel -match '(?i)^a') {
                $selectedPaths = @($inv | ForEach-Object { $_.Path })
            } else {
                $indexes = $sel -split '[,; ]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
                foreach ($idxStr in $indexes) {
                    $idxVal = 0
                    if ([int]::TryParse($idxStr, [ref]$idxVal) -and $idxVal -ge 1 -and $idxVal -le $inv.Count) {
                        $selectedPaths += $inv[$idxVal - 1].Path
                    }
                }
            }

            if ($selectedPaths.Count -eq 0) {
                Write-ModuleLog -Level WARN -Message "Seleccion vacia, descargando primera carpeta por defecto ($($inv[0].Path))." -LogFile $logFile
                $selectedPaths = @($inv[0].Path)
            }

            Invoke-AdbPullImages -AdbDevice $adbDevice -DevicePaths $selectedPaths -DestinationFolder $sourceDir -LogFile $logFile
        }
    }

    # Verificar que tengamos imagenes en source\
    $imagesToProcess = @(Get-ChildItem -Path $sourceDir -Recurse -File | Where-Object {
        $script:SupportedExtensions -contains $_.Extension.ToLower()
    })

    if ($imagesToProcess.Count -eq 0) {
        Write-ModuleLog -Level ERROR -Message "No se encontraron imagenes validas para procesar en $sourceDir" -LogFile $logFile
        return
    }

    $manifest.TotalImages = $imagesToProcess.Count
    Write-ModuleLog -Level SUCCESS -Message "Total de imagenes listas para extraccion EXIF: $($imagesToProcess.Count)" -LogFile $logFile

    # =================================================================
    # FASE 3: EXTRACCION EXIF
    # =================================================================
    $rawCsv = Join-Path $workdir 'raw_metadata.csv'
    $rawMeta = Invoke-ExifExtraction -SourceDirectory $sourceDir -RawCsvPath $rawCsv -LogFile $logFile

    # =================================================================
    # FASE 4: NORMALIZACION
    # =================================================================
    $normJson = Join-Path $workdir 'normalized_metadata.json'
    $normCsv = Join-Path $workdir 'normalized_metadata.csv'
    $normalized = Convert-MetadataNormalized -RawMetadata $rawMeta -SourceDirectory $sourceDir -ThumbsDirectory $thumbsDir -NormalizedJsonPath $normJson -NormalizedCsvPath $normCsv -LogFile $logFile

    # =================================================================
    # FASE 5: ANALISIS FORENSE AGREGADO
    # =================================================================
    $analysisJson = Join-Path $workdir 'analysis_summary.json'
    $analysis = Get-MetadataAnalysis -NormalizedItems $normalized -AnalysisJsonPath $analysisJson -LogFile $logFile

    # =================================================================
    # FASE 6: GENERACION DE MAPA Y DASHBOARD HTML
    # =================================================================
    $htmlOutputs = New-ForensicHtmlOutputs -NormalizedItems $normalized -AnalysisSummary $analysis -ManifestData $manifest -OutputDirectory $workdir -LogFile $logFile

    # =================================================================
    # FASE 7: CADENA DE CUSTODIA Y EVIDENCIA
    # =================================================================
    $manifestPath = Save-MetadataEvidence -OutputDirectory $workdir -ManifestData $manifest -LogFile $logFile

    # Opcional: Generar paquete ZIP final
    if (-not $SkipZipPackaging) {
        try {
            $zipOut = "$workdir.zip"
            Write-ModuleLog -Level INFO -Message "Comprimiendo reporte final de evidencia en: $zipOut" -LogFile $logFile
            # Excluir la carpeta source grande si el usuario lo desea o incluir resumen completo
            Compress-Archive -Path (Join-Path $workdir '*') -DestinationPath $zipOut -CompressionLevel Optimal -Force
            Write-ModuleLog -Level SUCCESS -Message "Paquete ZIP de evidencia creado con exito: $zipOut" -LogFile $logFile
        } catch {
            Write-ModuleLog -Level WARN -Message "Aviso: No se pudo crear el archivo ZIP: $($_.Exception.Message)" -LogFile $logFile
        }
    }

    # =================================================================
    # RESUMEN EN PANTALLA
    # =================================================================
    Write-Host "`n=====================================================================" -ForegroundColor Green
    Write-Host "   EXTRACCION Y ANALISIS DE METADATOS FOTOGRAFICOS COMPLETADO" -ForegroundColor Cyan
    Write-Host "=====================================================================" -ForegroundColor Green
    Write-Host " Directorio de Resultados : $workdir" -ForegroundColor White
    Write-Host " Total Imagenes           : $($analysis.TotalImages)" -ForegroundColor White
    Write-Host " Con Coordenadas GPS      : $($analysis.GpsImages) ($($analysis.GpsCoveragePercent)%)" -ForegroundColor $(if ($analysis.GpsImages -gt 0) { 'Green' } else { 'Yellow' })
    Write-Host " Sin Coordenadas GPS      : $($analysis.NoGpsImages)" -ForegroundColor White
    Write-Host " Rango de Fechas          : $($analysis.DateRange.Min) hasta $($analysis.DateRange.Max)" -ForegroundColor White
    Write-Host " Dispositivos Detectados  : $($analysis.Devices.Count)" -ForegroundColor White
    Write-Host " Clusters Geograficos     : $($analysis.TopClusters.Count)" -ForegroundColor White
    Write-Host " Dashboard Forense        : file:///$($htmlOutputs.Dashboard.Replace('\', '/'))" -ForegroundColor Cyan
    Write-Host " Mapa Interactivo Leaflet : file:///$($htmlOutputs.Map.Replace('\', '/'))" -ForegroundColor Cyan
    Write-Host "=====================================================================`n" -ForegroundColor Green

    # Abrir navegador si corresponde
    if (-not $SkipBrowser -and $htmlOutputs.Dashboard -and (Test-Path $htmlOutputs.Dashboard)) {
        try {
            Write-Host "Abriendo Dashboard Forense en el navegador..." -ForegroundColor Gray
            Start-Process $htmlOutputs.Dashboard
        } catch { }
    }
}

# ---------------------------------------------------------------------
# MENU INTERACTIVO
# ---------------------------------------------------------------------
function Show-InteractiveMenu {
    Clear-Host
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  MODULO FORENSE DE METADATOS FOTOGRAFICOS Y GEOLOCALIZACION (EXIF)  " -ForegroundColor Yellow
    Write-Host "  Android Tools Forensic Suite                                        " -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host " [1] Analizar una sola imagen" -ForegroundColor White
    Write-Host " [2] Analizar una carpeta local con imagenes" -ForegroundColor White
    Write-Host " [3] Analizar un archivo comprimido (.zip, .rar, .tar, .7z) [Directorio Dedicado]" -ForegroundColor White
    Write-Host " [4] Escanear fotos de un dispositivo Android (ADB)" -ForegroundColor White
    Write-Host " [5] Reutilizar fotos de un respaldo previo (Downloads\AndroidBackup)" -ForegroundColor White
    Write-Host " [6] Descargar fotos del dispositivo Android y analizar (Fresh Pull)" -ForegroundColor White
    Write-Host " [7] Descargar / Actualizar ExifTool portatil en metadata\" -ForegroundColor White
    Write-Host " [0] Salir" -ForegroundColor Gray
    Write-Host "======================================================================" -ForegroundColor Cyan

    $opt = Read-Host "Seleccione una opcion [0-7]"
    switch ($opt.Trim()) {
        '1' {
            $path = Read-Host "`nIngrese la ruta completa de la imagen"
            $clean = $path.Trim('"', "'")
            Start-MetadataExtractionFlow -SingleFile $clean
        }
        '2' {
            $path = Read-Host "`nIngrese la ruta de la carpeta local"
            $clean = $path.Trim('"', "'")
            Start-MetadataExtractionFlow -InputFolder $clean
        }
        '3' {
            $path = Read-Host "`nIngrese la ruta del archivo comprimido (.zip, .rar, .tar, .7z)"
            $clean = $path.Trim('"', "'")
            Start-MetadataExtractionFlow -ArchiveFile $clean
        }
        '4' {
            Start-MetadataExtractionFlow -AdbMode
        }
        '5' {
            Start-MetadataExtractionFlow -AdbMode -AdbReuse
        }
        '6' {
            Start-MetadataExtractionFlow -AdbMode -AdbFresh
        }
        '7' {
            Install-ExifToolPortable
            pause
            Show-InteractiveMenu
        }
        '0' {
            Write-Host "`nOperacion cancelada por el usuario." -ForegroundColor Gray
            exit 0
        }
        default {
            Write-Host "`nOpcion invalida." -ForegroundColor Red
            Start-Sleep -Seconds 1
            Show-InteractiveMenu
        }
    }
}

# ---------------------------------------------------------------------
# PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------
if ($DownloadExifTool) {
    Install-ExifToolPortable
    exit 0
}

if ($PSCmdlet.ParameterSetName -eq 'SingleImage') {
    Start-MetadataExtractionFlow -SingleFile $ImagePath -CustomOut $OutputDir -SkipBrowser:$NoOpenBrowser -SkipZipPackaging:$NoZip
} elseif ($PSCmdlet.ParameterSetName -eq 'Folder') {
    Start-MetadataExtractionFlow -InputFolder $FolderPath -CustomOut $OutputDir -SkipBrowser:$NoOpenBrowser -SkipZipPackaging:$NoZip
} elseif ($PSCmdlet.ParameterSetName -eq 'Archive') {
    Start-MetadataExtractionFlow -ArchiveFile $ArchivePath -CustomOut $OutputDir -SkipBrowser:$NoOpenBrowser -SkipZipPackaging:$NoZip
} elseif ($PSCmdlet.ParameterSetName -eq 'Device') {
    Start-MetadataExtractionFlow -AdbMode -AdbReuse:$Reuse -AdbFresh:$Fresh -CustomOut $OutputDir -SkipBrowser:$NoOpenBrowser -SkipZipPackaging:$NoZip
} else {
    Show-InteractiveMenu
}
