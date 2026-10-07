# Backup avanzado ADB sin root.
# Las consultas content:// no se consideran vCard/ICS reales: se guardan como
# texto raw y su estado se registra en MANIFEST.json.
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
Initialize-AdbLog | Out-Null
Write-AdbLog -Level INFO -Source 'android_advanced_backup.ps1' -Message 'Inicio de backup avanzado.'
try { $script:AndroidDevice = Select-CurrentAdbDevice -Persist } catch { Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red; exit 1 }

function Write-Utf8File {
    param([string]$Path, [object[]]$Lines)
    $text = (($Lines | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
    [IO.File]::WriteAllText($Path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-ExitCodeFromError {
    param([string]$Message)
    $match = [regex]::Match($Message, 'codigo\s+(?<code>-?\d+)')
    if ($match.Success) { return [int]$match.Groups['code'].Value }
    return 1
}

function Get-FailureStatus {
    param([string]$Message)
    if ($Message -match '(?i)securityexception|permission\s+denial|permission denied|not allowed|access denied|unauthorized') {
        return 'DENIED'
    }
    return 'ERROR'
}

function Invoke-BackupCategory {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][object[]]$Queries
    )

    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    Protect-BackupDirectory -Path $Directory | Out-Null
    $queryResults = @()
    $successfulQueries = 0
    $emptyQueries = 0
    $deniedQueries = 0
    $errorQueries = 0

    foreach ($query in $Queries) {
        $filePath = Join-Path $Directory $query.File
        $queryStatus = 'OK'
        $exitCode = 0
        $message = $null
        $lines = @()

        try {
            $lines = @(Invoke-AdbCommand -Serial $script:AndroidDevice.Serial -Arguments @('shell', [string]$query.Command))
            $meaningful = @($lines | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
            if ($meaningful.Count -eq 0) {
                $queryStatus = 'EMPTY'
                $emptyQueries++
                $message = 'ADB termino correctamente, pero no devolvio datos.'
            } else {
                $successfulQueries++
            }
            Write-Utf8File -Path $filePath -Lines $lines
        } catch {
            $message = $_.Exception.Message
            $exitCode = Get-ExitCodeFromError $message
            $queryStatus = Get-FailureStatus $message
            if ($queryStatus -eq 'DENIED') { $deniedQueries++ } else { $errorQueries++ }
            Write-Utf8File -Path $filePath -Lines @($message)
        }

        $queryResults += [ordered]@{
            Name      = [string]$query.Name
            File      = $filePath
            Command   = [string]$query.Command
            Status    = $queryStatus
            ExitCode  = $exitCode
            Message   = $message
        }
    }

    $status = if ($errorQueries -gt 0 -and $successfulQueries -eq 0) { 'ERROR' }
              elseif ($deniedQueries -gt 0 -and $successfulQueries -eq 0) { 'DENIED' }
              elseif ($successfulQueries -eq 0) { 'EMPTY' }
              elseif ($deniedQueries -gt 0 -or $errorQueries -gt 0 -or $emptyQueries -gt 0) { 'PARTIAL' }
              else { 'OK' }

    $categoryExitCode = if ($status -eq 'OK' -or $status -eq 'EMPTY') { 0 } else { 1 }
    return [pscustomobject][ordered]@{
        Name           = $Name
        Status         = $status
        ExitCode       = $categoryExitCode
        Directory      = $Directory
        QueryCount     = $Queries.Count
        Successful     = $successfulQueries
        Empty          = $emptyQueries
        Denied         = $deniedQueries
        Errors         = $errorQueries
        Queries        = @($queryResults)
    }
}

function Get-CategoryDefinitions {
    param([string]$Root)
    @(
        [pscustomobject]@{Name='Contactos'; Directory=(Join-Path $Root 'Contacts'); Queries=@(
            [pscustomobject]@{Name='contacts_raw'; File='contacts_raw.txt'; Command='content query --uri content://com.android.contacts/data/phones/'},
            [pscustomobject]@{Name='contacts_all'; File='contacts_all.txt'; Command='content query --uri content://com.android.contacts/contacts/'})},
        [pscustomobject]@{Name='SMS'; Directory=(Join-Path $Root 'SMS'); Queries=@(
            [pscustomobject]@{Name='sms_all'; File='sms_all.txt'; Command='content query --uri content://sms/'},
            [pscustomobject]@{Name='sms_inbox'; File='sms_inbox.txt'; Command='content query --uri content://sms/inbox'},
            [pscustomobject]@{Name='sms_sent'; File='sms_sent.txt'; Command='content query --uri content://sms/sent'},
            [pscustomobject]@{Name='sms_draft'; File='sms_draft.txt'; Command='content query --uri content://sms/draft'})},
        [pscustomobject]@{Name='Llamadas'; Directory=(Join-Path $Root 'Calls'); Queries=@(
            [pscustomobject]@{Name='call_log'; File='call_log.txt'; Command='content query --uri content://call_log/calls'})},
        [pscustomobject]@{Name='Calendario'; Directory=(Join-Path $Root 'Calendar'); Queries=@(
            [pscustomobject]@{Name='events'; File='calendar_events.txt'; Command='content query --uri content://com.android.calendar/events'},
            [pscustomobject]@{Name='calendars'; File='calendar_list.txt'; Command='content query --uri content://com.android.calendar/calendars'},
            [pscustomobject]@{Name='instances'; File='calendar_instances.txt'; Command='content query --uri content://com.android.calendar/instances/when'})},
        [pscustomobject]@{Name='Configuraciones'; Directory=(Join-Path $Root 'SystemSettings'); Queries=@(
            [pscustomobject]@{Name='global'; File='settings_global.txt'; Command='settings list global'},
            [pscustomobject]@{Name='system'; File='settings_system.txt'; Command='settings list system'},
            [pscustomobject]@{Name='secure'; File='settings_secure.txt'; Command='settings list secure'},
            [pscustomobject]@{Name='display'; File='display_config.txt'; Command='dumpsys display'},
            [pscustomobject]@{Name='battery'; File='battery_config.txt'; Command='dumpsys battery'})},
        [pscustomobject]@{Name='Apps'; Directory=(Join-Path $Root 'Apps'); Queries=@(
            [pscustomobject]@{Name='all'; File='apps_all.txt'; Command='pm list packages'},
            [pscustomobject]@{Name='user'; File='apps_user.txt'; Command='pm list packages -3'},
            [pscustomobject]@{Name='system'; File='apps_system.txt'; Command='pm list packages -s'},
            [pscustomobject]@{Name='packages'; File='packages_info.txt'; Command='dumpsys package'})},
        [pscustomobject]@{Name='WiFi'; Directory=(Join-Path $Root 'WiFi'); Queries=@(
            [pscustomobject]@{Name='saved_networks'; File='wifi_networks.txt'; Command='cmd wifi list-saved-networks'},
            [pscustomobject]@{Name='dump'; File='wifi_dump.txt'; Command='dumpsys wifi'})},
        [pscustomobject]@{Name='Bluetooth'; Directory=(Join-Path $Root 'Bluetooth'); Queries=@(
            [pscustomobject]@{Name='paired_devices'; File='bluetooth_devices.txt'; Command='dumpsys bluetooth_manager'})},
        [pscustomobject]@{Name='Diccionario'; Directory=(Join-Path $Root 'Dictionary'); Queries=@(
            [pscustomobject]@{Name='words'; File='user_dictionary.txt'; Command='content query --uri content://com.android.providers.userdictionary/words'})},
        [pscustomobject]@{Name='Clipboard'; Directory=(Join-Path $Root 'Clipboard'); Queries=@(
            [pscustomobject]@{Name='current'; File='clipboard_current.txt'; Command='service call clipboard 1'},
            [pscustomobject]@{Name='history'; File='clipboard_history.txt'; Command='dumpsys clipboard'})}
    )
}

function Write-BackupManifest {
    param([string]$Root, [object[]]$Categories)
    $failed = @($Categories | Where-Object { $_.Status -in @('DENIED','ERROR','PARTIAL','EMPTY') })
    $overall = if ($failed.Count -eq 0) { 'OK' } else { 'INCOMPLETE' }
    $manifest = [ordered]@{
        SchemaVersion = 1
        GeneratedUtc  = [DateTime]::UtcNow.ToString('o')
        DeviceSerial  = $script:AndroidDevice.Serial
        OverallStatus = $overall
        Complete      = ($overall -eq 'OK')
        Categories    = @($Categories)
    }
    $path = Join-Path $Root 'MANIFEST.json'
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
    return [pscustomobject]@{Path=$path; Status=$overall; Complete=($overall -eq 'OK')}
}

function Invoke-OneCategory {
    param([int]$Index)
    $root = Join-Path $env:USERPROFILE 'AndroidBackup\Advanced'
    $definition = @(Get-CategoryDefinitions -Root $root)[$Index]
    Write-AdbLog -Level INFO -Source 'android_advanced_backup.ps1:Invoke-OneCategory' -Serial $script:AndroidDevice.Serial -Message ("Inicio de categoria: {0}" -f $definition.Name)
    $result = Invoke-BackupCategory -Name $definition.Name -Directory $definition.Directory -Queries $definition.Queries
    $resultLevel = if ($result.Status -eq 'OK') { 'SUCCESS' } else { 'WARN' }
    Write-AdbLog -Level $resultLevel -Source 'android_advanced_backup.ps1:Invoke-OneCategory' -Serial $script:AndroidDevice.Serial -Message ("Categoria {0}: {1}, exit code {2}" -f $result.Name,$result.Status,$result.ExitCode)
    Write-Host ("{0}: {1}" -f $result.Name, $result.Status) -ForegroundColor $(if($result.Status -eq 'OK'){'Green'}else{'Yellow'})
    Write-Host ("Manifiesto de esta ejecucion: {0}" -f (Write-BackupManifest -Root $root -Categories @($result)).Path) -ForegroundColor Gray
    Read-Host 'Presiona Enter para continuar'
}

function Backup-AllAdvanced {
    if ((Read-Host 'Continuar con backup avanzado completo? (s/n)') -ne 's') { return }
    Write-AdbLog -Level INFO -Source 'android_advanced_backup.ps1:Backup-AllAdvanced' -Serial $script:AndroidDevice.Serial -Message 'Inicio de backup avanzado completo.'
    $root = Join-Path $env:USERPROFILE ("AndroidBackup\FullAdvanced_{0}" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    Protect-BackupDirectory -Path $root | Out-Null
    Write-Warning 'El contenido puede incluir datos sensibles y se guarda en texto plano; protege también el volumen y cifra el respaldo para almacenamiento externo.'
    $results = @()
    $definitions = @(Get-CategoryDefinitions -Root $root)
    for ($i=0; $i -lt $definitions.Count; $i++) {
        Write-Host ("[{0}/{1}] {2}" -f ($i+1),$definitions.Count,$definitions[$i].Name) -ForegroundColor Cyan
        $results += Invoke-BackupCategory -Name $definitions[$i].Name -Directory $definitions[$i].Directory -Queries $definitions[$i].Queries
    }
    $manifest = Write-BackupManifest -Root $root -Categories $results
    $summary = [ordered]@{
        GeneratedUtc  = [DateTime]::UtcNow.ToString('o')
        OverallStatus = $manifest.Status
        Complete      = $manifest.Complete
        Manifest      = $manifest.Path
        Categories    = @($results | ForEach-Object {
            [ordered]@{ Name=$_.Name; Status=$_.Status; ExitCode=$_.ExitCode }
        })
    }
    $summary | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $root 'RESUMEN_BACKUP.json') -Encoding UTF8
    if ($manifest.Complete) { Write-AdbLog -Level SUCCESS -Source 'android_advanced_backup.ps1:Backup-AllAdvanced' -Serial $script:AndroidDevice.Serial -Message ("Backup completo: {0}" -f $manifest.Path); Write-Host "BACKUP COMPLETO: todas las categorias OK. $($manifest.Path)" -ForegroundColor Green }
    else { Write-AdbLog -Level WARN -Source 'android_advanced_backup.ps1:Backup-AllAdvanced' -Serial $script:AndroidDevice.Serial -Message ("Backup incompleto: {0}" -f $manifest.Path); Write-Host "BACKUP INCOMPLETO: hay categorias EMPTY, DENIED, PARTIAL o ERROR. $($manifest.Path)" -ForegroundColor Red }
    Read-Host 'Presiona Enter para continuar'
}

function Show-AdvancedMenu {
    Clear-Host; Write-Host '========================================' -ForegroundColor Cyan; Write-Host ' ANDROID ADVANCED BACKUP (Sin Root)' -ForegroundColor Yellow
    Write-Host '1 Contactos (consulta raw, no vCard)'; Write-Host '2 SMS'; Write-Host '3 Llamadas'; Write-Host '4 Calendario'; Write-Host '5 Configuraciones'; Write-Host '6 Apps'; Write-Host '7 WiFi'; Write-Host '8 Bluetooth'; Write-Host '9 Diccionario'; Write-Host '10 Clipboard'; Write-Host '11 Backup completo'; Write-Host '12 Salir'
}

while ($true) {
    Show-AdvancedMenu
    switch (Read-Host 'Selecciona opcion (1-12)') {
        '1' { Invoke-OneCategory 0 }; '2' { Invoke-OneCategory 1 }; '3' { Invoke-OneCategory 2 }; '4' { Invoke-OneCategory 3 }; '5' { Invoke-OneCategory 4 }; '6' { Invoke-OneCategory 5 }; '7' { Invoke-OneCategory 6 }; '8' { Invoke-OneCategory 7 }; '9' { Invoke-OneCategory 8 }; '10' { Invoke-OneCategory 9 }; '11' { Backup-AllAdvanced }; '12' { Write-AdbLog -Level INFO -Source 'android_advanced_backup.ps1' -Message 'Fin de backup avanzado.'; exit 0 }; default { Write-Host 'Opcion invalida' -ForegroundColor Red; Start-Sleep 1 }
    }
}
