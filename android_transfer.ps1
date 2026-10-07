# Transferencias ADB con seleccion explicita y validacion estricta.
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'android_transfer.ps1' -Message 'Inicio de transferencias.'

try { $script:AndroidDevice = Select-CurrentAdbDevice -Persist } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red; exit 1 }

function Get-TransferDevice { try { $device = Get-CurrentAdbDevice; if ($null -eq $device -or $device.Status -cne 'device') { $device = Select-CurrentAdbDevice -Persist }; $device } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red; $null } }
function Invoke-Transfer {
    param([string]$Serial,[string[]]$Arguments)
    $operation = ($Arguments -join ' ')
    Write-AdbLog -Level INFO -Source 'android_transfer.ps1:Invoke-Transfer' -Serial $Serial -Message ("Inicio de transferencia: {0}" -f $operation)
    try { Invoke-AdbCommand -Serial $Serial -Arguments $Arguments | Out-Host; Write-AdbLog -Level SUCCESS -Source 'android_transfer.ps1:Invoke-Transfer' -Serial $Serial -Message ("Transferencia completada: {0}" -f $operation); $true }
    catch { Write-AdbLog -Level ERROR -Source 'android_transfer.ps1:Invoke-Transfer' -Serial $Serial -Message $_.Exception.Message; Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red; $false }
}
function New-TransferDir([string]$root) { $p=Join-Path $root (Get-Date -Format yyyyMMdd); New-Item -ItemType Directory -Path $p -Force|Out-Null; Protect-BackupDirectory -Path $p | Out-Null; $p }

function Show-Menu {
    Clear-Host; Write-Host '========================================' -ForegroundColor Cyan; Write-Host ' ANDROID TRANSFER INTERFACE' -ForegroundColor Yellow
    $d=Get-TransferDevice
    if($d){try{$m=Invoke-AdbCommand $d.Serial @('shell','getprop','ro.product.model');$a=Invoke-AdbCommand $d.Serial @('shell','getprop','ro.build.version.release');Write-Host "OK: estado device | Serial: $($d.Serial)" -ForegroundColor Green;Write-Host "Modelo: $($m -join ' ') | Android: $($a -join ' ')"}catch{Write-Host $_ -ForegroundColor Red}}
    Write-Host '1. Fotos/videos';Write-Host '2. Documentos';Write-Host '3. Subir archivos';Write-Host '4. Backup completo';Write-Host '5. Buscar';Write-Host '6. Espacio';Write-Host '7. Reconectar';Write-Host '8. Salir'
}
function Transfer-RecentPhotos {
    param([string]$Serial,[string]$Directory)
    $target = Join-Path $Directory 'DCIM\Camera'
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    try {
        $files = @(Invoke-AdbCommand -Serial $Serial -Arguments @('shell','find','/sdcard/DCIM/Camera','-type','f','-mtime','-7','-print') | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    } catch {
        Write-Host ("ERROR: no se pudieron buscar fotos recientes: {0}" -f $_.Exception.Message) -ForegroundColor Red
        return $false
    }
    if ($files.Count -eq 0) {
        Write-Host 'EMPTY: no hay fotos de los ultimos 7 dias.' -ForegroundColor Yellow
        return $false
    }
    $ok = $true
    foreach ($file in $files) {
        $ok = (Invoke-Transfer -Serial $Serial -Arguments @('pull',([string]$file).Trim(),$target)) -and $ok
    }
    return $ok
}

function Transfer-Photos {
    $d=Get-TransferDevice;if(!$d){Read-Host 'Presiona Enter';return};$c=Read-Host '1 recientes, 2 fotos, 3 videos, 4 ambos, 5 volver';if($c -eq '5'){return};$dir=New-TransferDir (Join-Path $env:USERPROFILE 'Downloads\AndroidBackup');$ok=$true
    switch($c){'1'{$ok=Transfer-RecentPhotos -Serial $d.Serial -Directory $dir};'2'{$ok=Invoke-Transfer $d.Serial @('pull','/sdcard/DCIM/',$dir)};'3'{$ok=Invoke-Transfer $d.Serial @('pull','/sdcard/DCIM/Camera/*.mp4',$dir);$ok=(Invoke-Transfer $d.Serial @('pull','/sdcard/Movies/',$dir))-and $ok};'4'{$x=Join-Path $dir DCIM;$y=Join-Path $dir Movies;New-Item -ItemType Directory -Path $x,$y -Force|Out-Null;$ok=Invoke-Transfer $d.Serial @('pull','/sdcard/DCIM/',$x);$ok=(Invoke-Transfer $d.Serial @('pull','/sdcard/Movies/',$y))-and $ok};default{Write-Host 'ERROR: opcion invalida' -ForegroundColor Red;return}}
    if($ok){Write-Host "OK: transferencia completada en $dir" -ForegroundColor Green};Read-Host 'Presiona Enter'
}
function Transfer-Documents {
    $d=Get-TransferDevice;if(!$d){Read-Host 'Presiona Enter';return};$c=Read-Host '1 recientes, 2 documentos, 3 PDFs, 4 Download, 5 volver';if($c -eq '5'){return};$dir=New-TransferDir (Join-Path $env:USERPROFILE 'Downloads\AndroidDocuments');$ok=$true
    switch($c){'1'{$fs=Invoke-AdbCommand $d.Serial @('shell','find','/sdcard/Download/','-type','f','-mtime','-7');foreach($f in $fs){if(([string]$f).Trim()){$ok=(Invoke-Transfer $d.Serial @('pull',([string]$f).Trim(),$dir))-and $ok}}};'2'{$ok=Invoke-Transfer $d.Serial @('pull','/sdcard/Documents/',$dir)};'3'{$fs=Invoke-AdbCommand $d.Serial @('shell','find','/sdcard/','-name','*.pdf','-type','f');foreach($f in $fs){if(([string]$f).Trim()){$ok=(Invoke-Transfer $d.Serial @('pull',([string]$f).Trim(),$dir))-and $ok}}};'4'{$ok=Invoke-Transfer $d.Serial @('pull','/sdcard/Download/',$dir)};default{Write-Host 'ERROR: opcion invalida' -ForegroundColor Red;return}}
    if($ok){Write-Host "OK: documentos transferidos en $dir" -ForegroundColor Green};Read-Host 'Presiona Enter'
}
function Upload-Files {
    $d=Get-TransferDevice;if(!$d){Read-Host 'Presiona Enter';return};$c=Read-Host '1 archivo, 2 carpeta, 3 listar Upload, 4 volver';switch($c){'1'{$p=Read-Host 'Ruta';if(Test-Path $p -PathType Leaf){if(Invoke-Transfer $d.Serial @('push',$p,'/sdcard/Upload/')){Write-Host 'OK: archivo subido' -ForegroundColor Green}}else{Write-Host 'ERROR: archivo no encontrado' -ForegroundColor Red}};'2'{$p=Read-Host 'Ruta';if(Test-Path $p -PathType Container){if(Invoke-Transfer $d.Serial @('push',$p,'/sdcard/Upload/')){Write-Host 'OK: carpeta subida' -ForegroundColor Green}}else{Write-Host 'ERROR: carpeta no encontrada' -ForegroundColor Red}};'3'{Invoke-Transfer $d.Serial @('shell','ls','-la','/sdcard/Upload/')|Out-Null}};if($c -ne '4'){Read-Host 'Presiona Enter'}
}
function Backup-Complete {
    Write-AdbLog -Level INFO -Source 'android_transfer.ps1:Backup-Complete' -Message 'Inicio de backup paralelo.'
    $d=Get-TransferDevice;if(!$d){Read-Host 'Presiona Enter';return};if((Read-Host 'Continuar? (s/n)') -ne 's'){return};$dir=Join-Path (Join-Path $env:USERPROFILE 'Downloads\AndroidFullBackup') ("backup_{0}" -f (Get-Date -Format yyyyMMdd_HHmmss));$folders=@('DCIM','Download','Documents','Music','Pictures');New-Item -ItemType Directory -Path $dir -Force|Out-Null;Protect-BackupDirectory -Path $dir|Out-Null;$module=Join-Path $PSScriptRoot 'AndroidAdb.psm1';$jobs=@()
    foreach($f in $folders){$local=Join-Path $dir $f;New-Item -ItemType Directory -Path $local -Force|Out-Null;$jobs+=Start-Job -ScriptBlock{param($m,$s,$r,$l,$n)try{Import-Module $m -Force;Invoke-AdbCommand $s @('pull',$r,$l)|Out-Null;[pscustomobject]@{Name=$n;Success=$true;Error=$null}}catch{[pscustomobject]@{Name=$n;Success=$false;Error=$_.Exception.Message}}} -ArgumentList $module,$d.Serial,"/sdcard/$f/",$local,$f}
    Wait-Job $jobs|Out-Null;$results=@(Receive-Job $jobs -ErrorAction SilentlyContinue);Remove-Job $jobs -Force -ErrorAction SilentlyContinue;$bad=@($results|Where-Object{-not $_.Success});if($bad.Count -eq 0 -and $results.Count -eq $jobs.Count){Write-Host "OK: backup completado en $dir" -ForegroundColor Green}else{Write-Host 'ERROR: fallo el backup:' -ForegroundColor Red;$bad|ForEach-Object{Write-Host "$($_.Name): $($_.Error)" -ForegroundColor Red}};Read-Host 'Presiona Enter'
}
while($true){Show-Menu;$o=Read-Host 'Selecciona opcion';switch($o){'1'{Transfer-Photos};'2'{Transfer-Documents};'3'{Upload-Files};'4'{Backup-Complete};'5'{$d=Get-TransferDevice;if($d){try{$q=ConvertTo-SafeAdbSearchPattern (Read-Host 'Texto');$pattern="*$q*";$results=@(Invoke-AdbCommand $d.Serial @('shell','find','/sdcard','-name',$pattern,'-print'))|Select-Object -First 20;$results|Out-Host}catch{Write-AdbLog -Level ERROR -Source 'android_transfer.ps1:Search' -Serial $d.Serial -Message $_.Exception.Message;Write-Host $_ -ForegroundColor Red}};Read-Host 'Presiona Enter'};'6'{$d=Get-TransferDevice;if($d){try{Invoke-AdbCommand $d.Serial @('shell','df','-h','/data','/sdcard')|Out-Host}catch{Write-AdbLog -Level ERROR -Source 'android_transfer.ps1:Space' -Serial $d.Serial -Message $_.Exception.Message;Write-Host $_ -ForegroundColor Red}};Read-Host 'Presiona Enter'};'7'{& (Join-Path $PSScriptRoot 'android_reconnect.ps1');Read-Host 'Presiona Enter'};'8'{Write-AdbLog -Level INFO -Source 'android_transfer.ps1' -Message 'Fin de transferencias.';exit 0};default{Write-Host 'ERROR: opcion invalida' -ForegroundColor Red}}}
