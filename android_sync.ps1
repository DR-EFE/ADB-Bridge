# Perfiles de sincronizacion Android.
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'android_sync.ps1' -Message 'Inicio de perfiles de sincronizacion.'

function Select-SyncProfile {
    $profiles=@(Get-AdbSyncProfiles)
    if(-not $profiles.Count){throw 'No hay perfiles guardados.'}
    for($i=0;$i -lt $profiles.Count;$i++){Write-Host ("[{0}] {1} -> {2}"-f ($i+1),$profiles[$i].Name,$profiles[$i].DestinationPath)}
    do{$n=0;$ok=[int]::TryParse((Read-Host 'Selecciona el perfil'),[ref]$n)}while(-not $ok -or $n -lt 1 -or $n -gt $profiles.Count)
    return $profiles[$n-1]
}
function Read-NullableInt([string]$Prompt){$s=Read-Host $Prompt;if([string]::IsNullOrWhiteSpace($s)){return $null};$v=0;if(-not[int]::TryParse($s,[ref]$v)-or $v -lt 0){throw 'Valor numerico invalido.'};return $v}
function New-ProfileFromPrompt {
    $name=Read-Host 'Nombre del perfil';$sources=(Read-Host 'Rutas Android separadas por coma, por ejemplo /sdcard/DCIM').Split(',')|ForEach-Object{$_.Trim()};$dest=Read-Host 'Carpeta local de destino';$extText=Read-Host 'Extensiones separadas por coma (vacio = todas)';$ext=if([string]::IsNullOrWhiteSpace($extText)){$null}else{@($extText.Split(',')|ForEach-Object{$x=$_.Trim().ToLowerInvariant();if($x -and $x[0] -ne '.'){$x=".$x"};$x})};$days=Read-NullableInt 'Ultimos dias (vacio = todos)';$min=Read-NullableInt 'Tamano minimo en bytes (vacio = cualquiera)';$max=Read-NullableInt 'Tamano maximo en bytes (vacio = cualquiera)';$overwrite=(Read-Host 'Sobrescribir existentes? (S/N)') -match '^(s|si|y|yes)$';return Add-AdbSyncProfile -Name $name -SourcePaths $sources -DestinationPath $dest -Extensions $ext -ModifiedWithinDays $days -MinSizeBytes $min -MaxSizeBytes $max -Overwrite $overwrite
}
try{$device=Select-CurrentAdbDevice -Persist;if(-not(Test-AdbDeviceReady $device $true)){throw 'El dispositivo no esta listo.'}}catch{Write-Host ("ERROR: {0}"-f $_.Exception.Message)-ForegroundColor Red;Write-AdbLog -Level ERROR -Source 'android_sync.ps1' -Message $_.Exception.Message;exit 1}
try{while($true){Write-Host '';Write-Host '1. Ejecutar perfil';Write-Host '2. Listar perfiles';Write-Host '3. Crear perfil';Write-Host '4. Eliminar perfil';Write-Host '5. Salir';switch(Read-Host 'Opcion'){'1'{$p=Select-SyncProfile;$r=Invoke-AdbSyncProfile -Profile $p -Device $device;$r|Format-List Profile,Copied,Skipped,Errors,Bytes;Read-Host 'Enter'|Out-Null};'2'{Get-AdbSyncProfiles|Format-Table Id,Name,SourcePaths,DestinationPath,Overwrite -AutoSize;Read-Host 'Enter'|Out-Null};'3'{New-ProfileFromPrompt|Format-List;Read-Host 'Enter'|Out-Null};'4'{$p=Select-SyncProfile;Remove-AdbSyncProfile -Id $p.Id;Write-Host 'Perfil eliminado.';};'5'{break};default{Write-Host 'Opcion invalida.' -ForegroundColor Yellow}}}}finally{Write-AdbLog -Level SUCCESS -Source 'android_sync.ps1' -Message 'Fin de perfiles de sincronizacion.'}
