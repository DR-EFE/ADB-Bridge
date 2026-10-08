Set-StrictMode -Version 2.0

# Se mantiene en el ámbito global para sobrevivir a Import-Module -Force durante
# la sesión actual de PowerShell. No se escribe en disco salvo que se solicite.
if (-not (Get-Variable -Name AndroidAdbCurrentDevice -Scope Global -ErrorAction SilentlyContinue)) {
    $global:AndroidAdbCurrentDevice = $null
}

if (-not (Get-Variable -Name AndroidAdbDebugLogging -Scope Global -ErrorAction SilentlyContinue)) {
    $global:AndroidAdbDebugLogging = $false
}

if (-not (Get-Variable -Name AndroidAdbLogInitialized -Scope Global -ErrorAction SilentlyContinue)) {
    $global:AndroidAdbLogInitialized = $false
}

function Get-AdbLogPath {
    [CmdletBinding()]
    param([datetime]$UtcDate)

    if ($null -eq $UtcDate -or $UtcDate -eq [datetime]::MinValue) { $UtcDate = [datetime]::UtcNow }
    $directory = Join-Path (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'logs') ''
    return (Join-Path $directory ("adb-tools-{0}.log" -f $UtcDate.ToUniversalTime().ToString('yyyy-MM-dd',[Globalization.CultureInfo]::InvariantCulture)))
}

function Initialize-AdbLog {
    [CmdletBinding()]
    param(
        [switch]$EnableDebug,
        [switch]$DisableDebug,
        [ValidateRange(1,3650)][int]$RetentionDays = 15
    )

    if ($EnableDebug -and $DisableDebug) { throw 'No se puede activar y desactivar DEBUG al mismo tiempo.' }
    if ($EnableDebug) { $global:AndroidAdbDebugLogging = $true }
    if ($DisableDebug) { $global:AndroidAdbDebugLogging = $false }

    $logPath = Get-AdbLogPath
    $logDirectory = Split-Path -Parent $logPath
    try {
        New-Item -ItemType Directory -Path $logDirectory -Force -ErrorAction Stop | Out-Null
        $cutoff = [datetime]::UtcNow.Date.AddDays(-$RetentionDays + 1)
        Get-ChildItem -LiteralPath $logDirectory -Filter 'adb-tools-*.log' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTimeUtc -lt $cutoff } |
            Remove-Item -Force -ErrorAction SilentlyContinue
        $global:AndroidAdbLogInitialized = $true
    } catch {
        $global:AndroidAdbLogInitialized = $false
        Write-Warning "No se pudo inicializar el log '$logPath': $($_.Exception.Message)"
    }
    return $logPath
}

function Write-AdbLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet('INFO','WARN','ERROR','DEBUG','SUCCESS')][string]$Level,
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Source = 'AndroidAdb.psm1',
        [string]$Serial
    )

    if ($Level -eq 'DEBUG' -and -not $global:AndroidAdbDebugLogging) { return }
    try {
        if (-not $global:AndroidAdbLogInitialized) { Initialize-AdbLog | Out-Null }
        if (-not $global:AndroidAdbLogInitialized) { return }
        $timestamp = [datetime]::UtcNow.ToString('o',[Globalization.CultureInfo]::InvariantCulture)
        $safeMessage = ([string]$Message) -replace "`r?`n", ' | '
        $serialText = if ([string]::IsNullOrWhiteSpace($Serial)) { '' } else { " [Serial=$Serial]" }
        $line = "{0} [{1}] [{2}]{3} {4}{5}" -f $timestamp,$Level,$Source,$serialText,$safeMessage,[Environment]::NewLine
        $mutex = New-Object -TypeName System.Threading.Mutex -ArgumentList $false,'Local\AndroidAdbLogMutex'
        $acquired = $false
        try {
            $acquired = $mutex.WaitOne(5000)
            if ($acquired) { [IO.File]::AppendAllText((Get-AdbLogPath),$line,(New-Object Text.UTF8Encoding($false))) }
        } finally {
            if ($acquired) { $mutex.ReleaseMutex() }
            $mutex.Dispose()
        }
    } catch {
        # El logging nunca debe impedir una operación ADB.
    }
}

function Get-AdbDevices {
    [CmdletBinding()]
    param()

    Initialize-AdbLog | Out-Null
    $output = @(& adb devices 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-AdbLog -Level ERROR -Source 'AndroidAdb.Get-AdbDevices' -Message ("adb devices fallo con codigo {0}: {1}" -f $exitCode,($output -join ' '))
        throw "adb devices fallo con codigo $exitCode. Salida: $($output -join ' ')"
    }

    foreach ($line in $output) {
        $text = [string]$line
        $match = [regex]::Match($text, '^\s*(?<serial>\S+)\s+(?<status>\S+)(?:\s+.*)?$')
        if ($match.Success -and $match.Groups['serial'].Value -cne 'List') {
            [pscustomobject]@{
                Serial = $match.Groups['serial'].Value
                Status = $match.Groups['status'].Value
                Raw    = $text.Trim()
            }
        }
    }
}

function Get-SingleReadyDevice {
    [CmdletBinding()]
    param()

    $ready = @(Get-AdbDevices | Where-Object { $_.Status -ceq 'device' })
    if ($ready.Count -eq 0) {
        throw 'No hay dispositivos ADB en estado exacto device.'
    }
    if ($ready.Count -gt 1) {
        $serials = $ready.Serial -join ', '
        throw "Hay mas de un dispositivo ADB listo ($($ready.Count)): $serials. Seleccion silenciosa cancelada."
    }
    return $ready[0]
}

function Resolve-AdbSerial {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [AllowNull()]
        [object]$DeviceOrEndpoint
    )

    if ($null -eq $DeviceOrEndpoint) { throw 'No se proporciono un dispositivo o endpoint ADB.' }
    if ($DeviceOrEndpoint -is [string]) {
        return (ConvertTo-AdbEndpoint -Endpoint ([string]$DeviceOrEndpoint)).Endpoint
    }

    # Un dispositivo guardado solo por USB no tiene LastEndpoint. En ese caso
    # UsbSerial es el identificador ADB válido y debe poder seleccionarse.
    foreach ($propertyName in @('Serial','LastEndpoint','Endpoint','UsbSerial')) {
        $property = $DeviceOrEndpoint.PSObject.Properties[$propertyName]
        if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
            $candidate = [string]$property.Value
            if ($propertyName -in @('Serial','UsbSerial') -and $candidate -notmatch ':\d+$') { return $candidate }
            try { return (ConvertTo-AdbEndpoint -Endpoint $candidate).Endpoint } catch { }
        }
    }
    throw 'El dispositivo no contiene Serial, LastEndpoint ni Endpoint utilizable.'
}

function Get-CurrentAdbDevice {
    [CmdletBinding()]
    param()

    if ($null -eq $global:AndroidAdbCurrentDevice) {
        try {
            $store = Read-AdbDeviceStore
            if ($store.CurrentDeviceId) {
                $saved = @($store.Devices | Where-Object { $_.Id -ceq $store.CurrentDeviceId })[0]
                if ($null -ne $saved) {
                    $ready = @(Get-AdbDevices | Where-Object {
                        $_.Serial -ieq $saved.LastEndpoint -or
                        ($saved.UsbSerial -and $_.Serial -ceq $saved.UsbSerial)
                    })[0]
                    if ($null -ne $ready -and $ready.Status -ceq 'device') {
                        $global:AndroidAdbCurrentDevice = [pscustomobject][ordered]@{
                            Id=$saved.Id; FriendlyName=$saved.FriendlyName; Serial=$ready.Serial; LastEndpoint=$saved.LastEndpoint; UsbSerial=$saved.UsbSerial
                            Status=$ready.Status; Mode=$saved.Mode; NetworkProfiles=@($saved.NetworkProfiles); Raw=$ready.Raw
                        }
                    }
                }
            }
        } catch { }
    }
    if ($null -eq $global:AndroidAdbCurrentDevice) { return $null }
    $serial = [string]$global:AndroidAdbCurrentDevice.Serial
    $record = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $serial })[0]
    if ($null -ne $record) {
        $global:AndroidAdbCurrentDevice.Status = $record.Status
        $global:AndroidAdbCurrentDevice.Raw = $record.Raw
    }
    return $global:AndroidAdbCurrentDevice
}

function Set-CurrentAdbDevice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [object]$Device,

        [switch]$Persist
    )

    $serial = Resolve-AdbSerial -DeviceOrEndpoint $Device
    $record = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $serial })[0]
    if ($null -eq $record -or $record.Status -cne 'device') {
        $state = if ($null -eq $record) { 'no encontrado' } else { $record.Status }
        throw "No se puede seleccionar '$serial': el estado actual es '$state'. Se requiere exactamente device."
    }

    $id = if ($Device.PSObject.Properties['Id']) { [string]$Device.Id } else { $null }
    $friendlyName = if ($Device.PSObject.Properties['FriendlyName']) { [string]$Device.FriendlyName } else { $serial }
    $mode = if ($Device.PSObject.Properties['Mode']) { [string]$Device.Mode } else { $null }
    $lastEndpoint = if ($Device.PSObject.Properties['LastEndpoint']) { [string]$Device.LastEndpoint } elseif ($serial -match ':\d+$') { $serial } else { $null }
    $usbSerial = if ($Device.PSObject.Properties['UsbSerial']) { [string]$Device.UsbSerial } else { $null }
    $networkProfiles = if ($Device.PSObject.Properties['NetworkProfiles']) { @($Device.NetworkProfiles) } else { @() }
    $global:AndroidAdbCurrentDevice = [pscustomobject][ordered]@{
        Id           = $id
        FriendlyName = $friendlyName
        Serial       = $serial
        LastEndpoint = $lastEndpoint
        UsbSerial    = $usbSerial
        Status       = $record.Status
        Mode         = $mode
        NetworkProfiles = $networkProfiles
        Raw          = $record.Raw
    }

    if ($Persist -and $id) {
        $store = Read-AdbDeviceStore
        if (@($store.Devices | Where-Object { $_.Id -ceq $id }).Count -ne 1) {
            throw "No se puede persistir la seleccion: no existe el Id '$id' en devices.json."
        }
        $store | Add-Member -NotePropertyName CurrentDeviceId -NotePropertyValue $id -Force
        Save-AdbDeviceStore -Store $store | Out-Null
    }
    return $global:AndroidAdbCurrentDevice
}

function Clear-CurrentAdbDevice {
    [CmdletBinding()]
    param([switch]$Persist)

    $global:AndroidAdbCurrentDevice = $null
    if ($Persist) {
        $store = Read-AdbDeviceStore
        $store | Add-Member -NotePropertyName CurrentDeviceId -NotePropertyValue $null -Force
        Save-AdbDeviceStore -Store $store | Out-Null
    }
}

function Test-AdbDeviceReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$Device,
        [bool]$AutoReconnect = $false
    )

    $serial = $null
    try {
        $serial = Resolve-AdbSerial -DeviceOrEndpoint $Device
        $stateOutput = @(Invoke-Adb -Serial $serial -Arguments @('get-state'))
        $state = (($stateOutput | ForEach-Object { [string]$_ }) -join ' ').Trim()
        if ($state -ceq 'device') {
            Write-AdbLog -Level SUCCESS -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $serial -Message 'Dispositivo listo: get-state=device.'
            return $true
        }
        Write-AdbLog -Level WARN -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $serial -Message ("Dispositivo no listo: get-state={0}." -f $state)
    } catch {
        Write-AdbLog -Level WARN -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $serial -Message $_.Exception.Message
    }

    if (-not $AutoReconnect) { return $false }

    $candidates = @()
    try {
        $lastEndpoint = if ($Device.PSObject.Properties['LastEndpoint']) { [string]$Device.LastEndpoint } else { $null }
        $profiles = if($Device.PSObject.Properties['NetworkProfiles']){@($Device.NetworkProfiles | Sort-Object LastUsedUtc -Descending)}else{@()}
        foreach($profile in $profiles){if(-not $profile.ForceUsbOnly -and -not [string]::IsNullOrWhiteSpace([string]$profile.PreferredEndpoint)){$candidates += [pscustomobject]@{Endpoint=$profile.PreferredEndpoint;ProfileId=$profile.Id}}}
        if (-not [string]::IsNullOrWhiteSpace($lastEndpoint)) { $candidates += [pscustomobject]@{Endpoint=$lastEndpoint;ProfileId=$null} }

        $mode = if ($Device.PSObject.Properties['Mode']) { [string]$Device.Mode } else { $null }
        if ($mode -eq 'wireless-debugging-tls') {
            $preferredHost = $null
            if ($lastEndpoint) { $preferredHost = (ConvertTo-AdbEndpoint $lastEndpoint).Host }
            $mdnsEndpoint = Get-AdbMdnsConnectionEndpoint -PreferredHost $preferredHost
            if ($null -ne $mdnsEndpoint) { $candidates += [pscustomobject]@{Endpoint=$mdnsEndpoint.Endpoint;ProfileId=$null} }
        }
    } catch {
        Write-AdbLog -Level WARN -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $serial -Message ("No se pudo descubrir un endpoint para reconexion: {0}" -f $_.Exception.Message)
    }

    foreach ($candidateInfo in @($candidates | Group-Object Endpoint | ForEach-Object { $_.Group[0] })) {
        $candidate = [string]$candidateInfo.Endpoint
        try {
            Write-AdbLog -Level INFO -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $candidate -Message 'Intentando reconexion automatica.'
            $record = Connect-AdbEndpoint -Endpoint $candidate -TimeoutSeconds 10
            $verified = @(Invoke-Adb -Serial $record.Serial -Arguments @('get-state'))
            $verifiedState = (($verified | ForEach-Object { [string]$_ }) -join ' ').Trim()
            if ($verifiedState -ceq 'device') {
                if ($Device.PSObject.Properties['Id'] -and $Device.Id) {
                    try { Update-SavedAdbDevice -Id ([string]$Device.Id) -LastEndpoint $record.Serial -LastConnectedUtc ([DateTime]::UtcNow.ToString('o')) | Out-Null } catch { }
                    if($candidateInfo.ProfileId){try{Update-SavedAdbNetworkProfile -DeviceId ([string]$Device.Id) -ProfileId ([string]$candidateInfo.ProfileId) -PreferredEndpoint $record.Serial -LastUsedUtc ([DateTime]::UtcNow.ToString('o'))|Out-Null}catch{}}
                }
                Set-CurrentAdbDevice -Device ([pscustomobject]@{
                    Id = if ($Device.PSObject.Properties['Id']) { $Device.Id } else { $null }
                    FriendlyName = if ($Device.PSObject.Properties['FriendlyName']) { $Device.FriendlyName } else { $record.Serial }
                    Mode = if ($Device.PSObject.Properties['Mode']) { $Device.Mode } else { $null }
                    NetworkProfiles = if ($Device.PSObject.Properties['NetworkProfiles']) { @($Device.NetworkProfiles) } else { @() }
                    Serial = $record.Serial
                }) | Out-Null
                Write-AdbLog -Level SUCCESS -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $record.Serial -Message 'Reconexión automática verificada: get-state=device.'
                return $true
            }
            Write-AdbLog -Level WARN -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $candidate -Message ("Reconexion no lista: get-state={0}." -f $verifiedState)
        } catch {
            Write-AdbLog -Level WARN -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $candidate -Message ("Fallo de reconexion: {0}" -f $_.Exception.Message)
        }
    }

    Write-AdbLog -Level ERROR -Source 'AndroidAdb.Test-AdbDeviceReady' -Serial $serial -Message 'El dispositivo sigue sin estar listo después de la reconexión automática.'
    return $false
}

function Select-CurrentAdbDevice {
    [CmdletBinding()]
    param([switch]$Persist)

    $current = Get-CurrentAdbDevice
    if ($null -ne $current -and $current.Status -ceq 'device') { return $current }

    $saved = @(Get-SavedAdbDevices)
    $ready = @(Get-AdbDevices | Where-Object { $_.Status -ceq 'device' })
    if ($saved.Count -eq 0) {
        if ($ready.Count -eq 0) { throw 'No hay dispositivos guardados ni dispositivos ADB en estado device.' }
        Write-Host 'DISPOSITIVOS ADB DISPONIBLES' -ForegroundColor Cyan
        for ($index = 0; $index -lt $ready.Count; $index++) {
            Write-Host ("[{0}] {1}" -f ($index + 1),$ready[$index].Serial) -ForegroundColor Green
        }
        do {
            $selection = 0
            $valid = [int]::TryParse((Read-Host 'Selecciona el dispositivo'),[ref]$selection)
        } while (-not $valid -or $selection -lt 1 -or $selection -gt $ready.Count)
        return Set-CurrentAdbDevice -Device $ready[$selection - 1] -Persist:$Persist
    }

    Write-Host 'DISPOSITIVOS GUARDADOS DISPONIBLES' -ForegroundColor Cyan
    for ($index = 0; $index -lt $saved.Count; $index++) {
        $savedDevice = $saved[$index]
        $endpoint = if ($savedDevice.LastEndpoint) { $savedDevice.LastEndpoint } else { '(sin endpoint)' }
        $active = @($ready | Where-Object {
            $_.Serial -ieq $savedDevice.LastEndpoint -or
            ($savedDevice.UsbSerial -and $_.Serial -ceq $savedDevice.UsbSerial)
        })[0]
        $state = if ($null -eq $active) { 'no conectado' } else { $active.Status }
        Write-Host ("[{0}] {1} | {2} | {3}" -f ($index + 1),$savedDevice.FriendlyName,$endpoint,$state) -ForegroundColor Green
    }
    do {
        $selection = 0
        $valid = [int]::TryParse((Read-Host 'Selecciona el dispositivo guardado'),[ref]$selection)
    } while (-not $valid -or $selection -lt 1 -or $selection -gt $saved.Count)

    $selected = $saved[$selection - 1]
    $active = @($ready | Where-Object {
        $_.Serial -ieq $selected.LastEndpoint -or
        ($selected.UsbSerial -and $_.Serial -ceq $selected.UsbSerial)
    })[0]
    if ($null -eq $active) {
        throw "'$($selected.FriendlyName)' no esta conectado en estado device. Ejecuta android_reconnect.ps1 antes de continuar."
    }
    return Set-CurrentAdbDevice -Device ([pscustomobject][ordered]@{
        Id=$selected.Id; FriendlyName=$selected.FriendlyName; Mode=$selected.Mode; UsbSerial=$selected.UsbSerial; LastEndpoint=$selected.LastEndpoint; Serial=$active.Serial
    }) -Persist:$Persist
}

function Invoke-AdbCommand {
    [CmdletBinding()]
    param(
        [Parameter(Position = 1)]
        [ValidateNotNull()]
        [string[]]$Arguments,

        [Parameter(Position = 0)]
        [string]$Serial,

        [object]$Device
    )

    $selected = if ($PSBoundParameters.ContainsKey('Device')) {
        Set-CurrentAdbDevice -Device $Device
    } elseif ($PSBoundParameters.ContainsKey('Serial')) {
        Set-CurrentAdbDevice -Device ([pscustomobject]@{ Serial=$Serial })
    } else {
        Get-CurrentAdbDevice
    }
    if ($null -eq $selected) {
        throw 'No hay dispositivo seleccionado. Ejecuta Select-CurrentAdbDevice o elige un dispositivo desde el menu.'
    }
    $serial = Resolve-AdbSerial -DeviceOrEndpoint $selected
    $record = @(Get-AdbDevices | Where-Object { $_.Serial -ceq $serial })[0]
    if ($null -eq $record -or $record.Status -cne 'device') {
        $state = if ($null -eq $record) { 'no encontrado' } else { $record.Status }
        throw "El dispositivo seleccionado '$serial' no esta listo. Estado: $state"
    }
    if (-not (Test-AdbDeviceReady -Device $selected -AutoReconnect $true)) {
        throw "El dispositivo seleccionado '$serial' no esta listo. No se ejecutara el comando ADB."
    }
    $selected = Get-CurrentAdbDevice
    $serial = Resolve-AdbSerial -DeviceOrEndpoint $selected
    if ($null -eq $Arguments) { $Arguments = @() }
    return Invoke-Adb -Serial $serial -Arguments $Arguments
}

function Invoke-Adb {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Serial,

        [Parameter(Mandatory = $true, Position = 0)]
        [ValidateNotNull()]
        [string[]]$Arguments
    )

    Initialize-AdbLog | Out-Null
    $commandText = ($Arguments -join ' ')
    Write-AdbLog -Level INFO -Source 'AndroidAdb.Invoke-Adb' -Serial $Serial -Message ("Inicio ADB: {0}" -f $commandText)
    $adbArguments = @('-s', $Serial) + $Arguments
    $output = @(& adb @adbArguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $detail = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
        Write-AdbLog -Level ERROR -Source 'AndroidAdb.Invoke-Adb' -Serial $Serial -Message ("ADB fallo con codigo {0}: {1}" -f $exitCode,$detail)
        throw "adb -s '$Serial' fallo con codigo $exitCode. $detail"
    }
    Write-AdbLog -Level SUCCESS -Source 'AndroidAdb.Invoke-Adb' -Serial $Serial -Message ("ADB completado: {0}" -f $commandText)
    return $output
}

function Get-AdbMediaProcessArgumentString {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        $value = [string]$_
        if ($value -match '^[A-Za-z0-9_./:\[\]-]+$') { $value } else { '"' + ($value -replace '"','\\"') + '"' }
    }) -join ' ')
}

function Capture-AdbScreenshot {
    [CmdletBinding()]
    param(
        [object]$Device,
        [string]$OutputPath,
        [switch]$OpenAfterCapture
    )

    $selected = if ($PSBoundParameters.ContainsKey('Device')) { Set-CurrentAdbDevice -Device $Device } else { Get-CurrentAdbDevice }
    if ($null -eq $selected) { throw 'No hay dispositivo seleccionado para capturar la pantalla.' }
    if (-not (Test-AdbDeviceReady -Device $selected -AutoReconnect $true)) { throw 'El dispositivo no esta listo para capturar la pantalla.' }
    $serial = Resolve-AdbSerial -DeviceOrEndpoint (Get-CurrentAdbDevice)
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        $directory = Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'screenshots'
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        $OutputPath = Join-Path $directory ("screenshot-{0}.png" -f [DateTime]::Now.ToString('yyyyMMdd-HHmmss'))
        if (Test-Path -LiteralPath $OutputPath) { $OutputPath = Join-Path $directory ("screenshot-{0}-{1}.png" -f [DateTime]::Now.ToString('yyyyMMdd-HHmmss'),[guid]::NewGuid().ToString('N').Substring(0,6)) }
    } else {
        $OutputPath = [IO.Path]::GetFullPath($OutputPath)
        New-Item -ItemType Directory -Path (Split-Path -Parent $OutputPath) -Force | Out-Null
    }
    $outputStream = $null
    $process = $null
    try {
        Write-AdbLog -Level INFO -Source 'AndroidAdb.Capture-AdbScreenshot' -Serial $serial -Message ("Capturando pantalla en {0}." -f $OutputPath)
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = 'adb'
        $psi.Arguments = Get-AdbMediaProcessArgumentString @('-s',$serial,'exec-out','screencap','-p')
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $psi
        if (-not $process.Start()) { throw 'No se pudo iniciar adb para capturar la pantalla.' }
        $outputStream = [IO.File]::Open($OutputPath,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $process.StandardOutput.BaseStream.CopyTo($outputStream)
        $errorText = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "adb exec-out screencap fallo con codigo $($process.ExitCode). $errorText" }
        if ((Get-Item -LiteralPath $OutputPath).Length -eq 0) { throw 'adb no produjo datos de imagen.' }
        Write-AdbLog -Level SUCCESS -Source 'AndroidAdb.Capture-AdbScreenshot' -Serial $serial -Message 'Captura completada.'
        if ($OpenAfterCapture) { Start-Process -FilePath $OutputPath | Out-Null }
        return (Get-Item -LiteralPath $OutputPath)
    } catch {
        if (Test-Path -LiteralPath $OutputPath -PathType Leaf) { Remove-Item -LiteralPath $OutputPath -Force -ErrorAction SilentlyContinue }
        Write-AdbLog -Level ERROR -Source 'AndroidAdb.Capture-AdbScreenshot' -Serial $serial -Message $_.Exception.Message
        throw
    } finally {
        if ($null -ne $outputStream) { $outputStream.Dispose() }
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Start-AdbScreenRecording {
    [CmdletBinding()]
    param(
        [object]$Device,
        [ValidateRange(1,86400)][int]$DurationSeconds = 180,
        [string]$OutputPath
    )

    $selected = if ($PSBoundParameters.ContainsKey('Device')) { Set-CurrentAdbDevice -Device $Device } else { Get-CurrentAdbDevice }
    if ($null -eq $selected) { throw 'No hay dispositivo seleccionado para grabar la pantalla.' }
    if (-not (Test-AdbDeviceReady -Device $selected -AutoReconnect $true)) { throw 'El dispositivo no esta listo para grabar la pantalla.' }
    $serial = Resolve-AdbSerial -DeviceOrEndpoint (Get-CurrentAdbDevice)
    $directory = Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'recordings'
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path $directory ("recording-{0}.mp4" -f [DateTime]::Now.ToString('yyyyMMdd-HHmmss')) }
    $OutputPath = [IO.Path]::GetFullPath($OutputPath)
    New-Item -ItemType Directory -Path (Split-Path -Parent $OutputPath) -Force | Out-Null
    $remotePath = '/sdcard/android-tools-recording-{0}.mp4' -f [guid]::NewGuid().ToString('N')
    $process = $null
    $stoppedByUser = $false
    try {
        Write-AdbLog -Level INFO -Source 'AndroidAdb.Start-AdbScreenRecording' -Serial $serial -Message ("Grabacion iniciada; limite {0}s." -f $DurationSeconds)
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = 'adb'
        $psi.Arguments = Get-AdbMediaProcessArgumentString @('-s',$serial,'shell','screenrecord','--time-limit',[string]$DurationSeconds,$remotePath)
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardError = $true
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $psi
        if (-not $process.Start()) { throw 'No se pudo iniciar adb shell screenrecord.' }
        $started = [DateTime]::UtcNow
        while (-not $process.HasExited) {
            $elapsed = ([DateTime]::UtcNow - $started).TotalSeconds
            Write-Progress -Activity 'Grabando pantalla Android' -Status ("{0}s / {1}s. Ctrl+C para detener." -f [int]$elapsed,$DurationSeconds) -PercentComplete ([Math]::Min(100,[int](100 * $elapsed / $DurationSeconds)))
            if ([Console]::IsInputRedirected -eq $false -and [Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ([int]$key.KeyChar -eq 3) { $stoppedByUser = $true; $process.Kill(); break }
            }
            Start-Sleep -Milliseconds 250
        }
        $process.WaitForExit()
        Write-Progress -Activity 'Grabando pantalla Android' -Completed
        if (-not $stoppedByUser -and $process.ExitCode -ne 0) { $errorText = $process.StandardError.ReadToEnd(); throw "screenrecord fallo con codigo $($process.ExitCode). $errorText" }
        Invoke-AdbCommand -Serial $serial -Arguments @('pull',$remotePath,$OutputPath) | Out-Null
        if (-not (Test-Path -LiteralPath $OutputPath) -or (Get-Item -LiteralPath $OutputPath).Length -eq 0) { throw 'adb pull no produjo un video valido.' }
        Write-AdbLog -Level SUCCESS -Source 'AndroidAdb.Start-AdbScreenRecording' -Serial $serial -Message ("Grabacion guardada en {0}." -f $OutputPath)
        return (Get-Item -LiteralPath $OutputPath)
    } catch {
        Write-AdbLog -Level ERROR -Source 'AndroidAdb.Start-AdbScreenRecording' -Serial $serial -Message $_.Exception.Message
        throw
    } finally {
        Write-Progress -Activity 'Grabando pantalla Android' -Completed
        if ($null -ne $process) { $process.Dispose() }
        try { Invoke-AdbCommand -Serial $serial -Arguments @('shell','rm','-f',$remotePath) | Out-Null } catch { Write-AdbLog -Level WARN -Source 'AndroidAdb.Start-AdbScreenRecording' -Serial $serial -Message ("No se pudo eliminar el temporal remoto: {0}" -f $_.Exception.Message) }
    }
}

function Test-ScrcpyAvailable {
    [CmdletBinding()]
    param()
    return ($null -ne (Get-Command scrcpy -ErrorAction SilentlyContinue))
}

function Start-AdbScrcpy {
    [CmdletBinding()]
    param([object]$Device)
    if (-not (Test-ScrcpyAvailable)) { throw 'scrcpy no esta instalado o no esta en PATH.' }
    $selected = if ($PSBoundParameters.ContainsKey('Device')) { Set-CurrentAdbDevice -Device $Device } else { Get-CurrentAdbDevice }
    if ($null -eq $selected -or -not (Test-AdbDeviceReady -Device $selected -AutoReconnect $true)) { throw 'El dispositivo no esta listo para scrcpy.' }
    $serial = Resolve-AdbSerial -DeviceOrEndpoint (Get-CurrentAdbDevice)
    Write-AdbLog -Level INFO -Source 'AndroidAdb.Start-AdbScrcpy' -Serial $serial -Message 'Iniciando scrcpy.'
    return Start-Process -FilePath 'scrcpy' -ArgumentList @('-s',$serial) -PassThru
}

function ConvertTo-SafeAdbSearchPattern {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Value)

    $value = $Value.Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 64) {
        throw 'La busqueda debe tener entre 1 y 64 caracteres.'
    }
    if ($value -notmatch '^[A-Za-z0-9._* -]+$') {
        throw 'La busqueda solo permite letras, numeros, espacio, punto, guion, guion bajo y asterisco.'
    }
    return $value
}

function ConvertTo-SafeAndroidPath {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $value = $Path.Trim().TrimEnd('/')
    if ($value -notmatch '^/(sdcard|storage|data|system|cache)(/[A-Za-z0-9._ -]+)*$' -or $value -match '(^|/)\.\.(/|$)') {
        throw "Ruta Android no permitida: $Path"
    }
    return $value
}

function ConvertTo-SafeWindowsName {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = $Name.Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 80) {
        throw 'El nombre debe tener entre 1 y 80 caracteres.'
    }
    if ($value -notmatch '^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$' -or $value -in @('.', '..') -or $value -match '[\. ]$') {
        throw "Nombre de archivo/carpeta no permitido: $Name"
    }
    if ($value -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$') {
        throw "Nombre reservado de Windows: $Name"
    }
    return $value
}

function Protect-BackupDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        if (-not (Test-Path -LiteralPath $Path)) {
            New-Item -ItemType Directory -Path $Path -Force | Out-Null
        }
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
        $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        $propagate = [Security.AccessControl.PropagationFlags]::None
        $allow = [Security.AccessControl.AccessControlType]::Allow
        foreach ($identity in @("$env:USERDOMAIN\$env:USERNAME", 'SYSTEM')) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inherit, $propagate, $allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
        return $true
    } catch {
        Write-Warning "No se pudo proteger ACL de '$Path'. Los respaldos pueden quedar accesibles para otros usuarios: $($_.Exception.Message)"
        return $false
    }
}

function Test-AdbAvailable {
    [CmdletBinding()]
    param()

    if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
        throw 'adb no se encuentra en PATH. Instala Android Platform Tools o agrega su carpeta a PATH.'
    }
    return $true
}

function Invoke-AdbHost {
    <#
    .SYNOPSIS
    Ejecuta una orden ADB que no puede llevar -s, por ejemplo pair, connect o mdns.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    Initialize-AdbLog | Out-Null
    Test-AdbAvailable | Out-Null
    $displayArguments = if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'pair' -and $Arguments.Count -ge 3) {
        @('pair',$Arguments[1],'<codigo oculto>') -join ' '
    } else { $Arguments -join ' ' }
    Write-AdbLog -Level INFO -Source 'AndroidAdb.Invoke-AdbHost' -Message ("Inicio ADB host: {0}" -f $displayArguments)
    $output = @(& adb @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $detail = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
        Write-AdbLog -Level ERROR -Source 'AndroidAdb.Invoke-AdbHost' -Message ("ADB host fallo con codigo {0}: {1}" -f $exitCode,$detail)
        throw "adb $($Arguments -join ' ') fallo con codigo $exitCode. $detail"
    }
    Write-AdbLog -Level SUCCESS -Source 'AndroidAdb.Invoke-AdbHost' -Message ("ADB host completado: {0}" -f $displayArguments)
    return $output
}

function ConvertTo-AdbEndpoint {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Endpoint)

    $value = $Endpoint.Trim()
    $match = [regex]::Match($value, '^\[(?<host>[^\]]+)\]:(?<port>\d{1,5})$')
    if (-not $match.Success) {
        $match = [regex]::Match($value, '^(?<host>[^:\s]+):(?<port>\d{1,5})$')
    }
    if (-not $match.Success) { throw "Endpoint ADB invalido: $Endpoint" }

    $port = [int]$match.Groups['port'].Value
    if ($port -lt 1 -or $port -gt 65535) { throw "Puerto ADB invalido: $Endpoint" }
    $host = $match.Groups['host'].Value
    if ([string]::IsNullOrWhiteSpace($host) -or $host -match '\s') { throw "Host ADB invalido: $Endpoint" }
    $normalized = if ($host.Contains(':')) { "[{0}]:{1}" -f $host,$port } else { "{0}:{1}" -f $host,$port }
    return [pscustomobject]@{ Host=$host; Port=$port; Endpoint=$normalized }
}

function Get-AndroidToolsConfigPath {
    [CmdletBinding()]
    param()
    return (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'android_config.json')
}

function Save-AdbConnectionConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet('WirelessTls','LegacyTcpIp')][string]$Mode,
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$Serial
    )

    $parts = ConvertTo-AdbEndpoint $Endpoint
    $path = Get-AndroidToolsConfigPath
    $directory = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $config = [ordered]@{
        SchemaVersion = 1
        Mode          = $Mode
        Host          = $parts.Host
        Port          = $parts.Port
        Endpoint      = $parts.Endpoint
        Serial        = $Serial
        UpdatedUtc    = [DateTime]::UtcNow.ToString('o')
    }
    $json = $config | ConvertTo-Json -Depth 3
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding($false)))
    return $path
}

function Read-AdbConnectionConfig {
    [CmdletBinding()]
    param()

    $path = Get-AndroidToolsConfigPath
    if (-not (Test-Path -LiteralPath $path)) { throw "No existe la configuracion ADB: $path" }
    try { $config = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { throw "No se pudo leer '$path': $($_.Exception.Message)" }

    foreach ($name in @('SchemaVersion','Mode','Endpoint','Serial','UpdatedUtc')) {
        if ($null -eq $config.$name -or [string]::IsNullOrWhiteSpace([string]$config.$name)) {
            throw "La configuracion ADB no contiene '$name'."
        }
    }
    # ModernPairing fue el nombre usado por versiones anteriores del proyecto.
    if ([string]$config.Mode -eq 'ModernPairing') { $config.Mode = 'WirelessTls' }
    if ([string]$config.Mode -notin @('WirelessTls','LegacyTcpIp')) { throw "Modo de conexion no admitido: $($config.Mode)" }
    $parts = ConvertTo-AdbEndpoint ([string]$config.Endpoint)
    $config | Add-Member -NotePropertyName EndpointParts -NotePropertyValue $parts -Force
    $config | Add-Member -NotePropertyName ConfigPath -NotePropertyValue $path -Force
    return $config
}

function Get-AdbDeviceRecord {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Serial)
    return @(Get-AdbDevices | Where-Object { $_.Serial -ceq $Serial })[0]
}

function Wait-AdbDevice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Serial,
        [ValidateRange(1,120)][int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $record = Get-AdbDeviceRecord -Serial $Serial
        if ($null -ne $record -and $record.Status -ceq 'device') { return $record }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    $last = Get-AdbDeviceRecord -Serial $Serial
    $state = if ($null -eq $last) { 'no encontrado' } else { $last.Status }
    Write-AdbLog -Level ERROR -Source 'AndroidAdb.Wait-AdbDevice' -Serial $Serial -Message ("No se alcanzo estado device. Estado final: {0}" -f $state)
    throw "El serial '$Serial' no alcanzo el estado exacto device. Estado final: $state"
}

function Connect-AdbEndpoint {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [ValidateRange(1,120)][int]$TimeoutSeconds = 15
    )

    $parts = ConvertTo-AdbEndpoint $Endpoint
    $connectOutput = @(Invoke-AdbHost -Arguments @('connect', $parts.Endpoint))
    $connectText = ($connectOutput | ForEach-Object { [string]$_ }) -join ' '
    if ($connectText -match '(?i)cannot connect|failed to connect|unable to connect|connection refused|timed out|no route|10013') {
        throw "adb connect no pudo alcanzar $($parts.Endpoint). Salida: $connectText"
    }
    return Wait-AdbDevice -Serial $parts.Endpoint -TimeoutSeconds $TimeoutSeconds
}

function Get-AdbMdnsConnectionEndpoint {
    [CmdletBinding()]
    param([string]$PreferredHost)

    $lines = @(Invoke-AdbHost -Arguments @('mdns','services'))
    $candidates = @()
    foreach ($line in $lines) {
        $text = [string]$line
        if ($text -notmatch 'adb-tls-connect') { continue }
        $match = [regex]::Match($text, '(?<endpoint>\[[^\]]+\]:\d{1,5}|[A-Za-z0-9._-]+:\d{1,5})')
        if (-not $match.Success) { continue }
        try { $candidate = ConvertTo-AdbEndpoint $match.Groups['endpoint'].Value } catch { continue }
        if (-not [string]::IsNullOrWhiteSpace($PreferredHost) -and $candidate.Host -ieq $PreferredHost) { return $candidate }
        $candidates += $candidate
    }
    if ($candidates.Count -eq 1) { return $candidates[0] }
    return $null
}

function Get-AdbDeviceStorePath {
    [CmdletBinding()]
    param()
    return (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'devices.json')
}

function New-AdbDeviceStore {
    [CmdletBinding()]
    param()
    return [pscustomobject][ordered]@{
        SchemaVersion = 1
        UpdatedUtc    = [DateTime]::UtcNow.ToString('o')
        CurrentDeviceId = $null
        Devices       = @()
    }
}

function Assert-AdbFriendlyName {
    param([string]$FriendlyName)
    $value = if ($null -eq $FriendlyName) { '' } else { $FriendlyName.Trim() }
    if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 80 -or $value -match '[\x00-\x1F]') {
        throw 'El nombre amigable debe tener entre 1 y 80 caracteres y no contener caracteres de control.'
    }
    return $value
}

function ConvertTo-AdbUtcTimestamp {
    param([AllowNull()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $parsed = [DateTimeOffset]::MinValue
    $valid = [DateTimeOffset]::TryParse(
        $Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    )
    if (-not $valid) { throw "Fecha UTC invalida en devices.json: $Value" }
    return $parsed.UtcDateTime.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
}

function ConvertTo-AdbBoolean {
    param([object]$Value,[string]$FieldName)
    if ($Value -is [bool]) { return [bool]$Value }
    $parsed = $false
    if ($null -ne $Value -and [bool]::TryParse([string]$Value,[ref]$parsed)) { return $parsed }
    throw "El campo '$FieldName' debe ser true o false."
}

function ConvertTo-AdbNetworkProfile {
    param([object]$Profile)
    foreach($field in @('Id','Name','PreferredEndpoint','ForceUsbOnly','LastUsedUtc','Notes')) { if($null -eq $Profile.PSObject.Properties[$field]) { throw "NetworkProfiles contiene un perfil sin el campo '$field'." } }
    $endpoint=if([string]::IsNullOrWhiteSpace([string]$Profile.PreferredEndpoint)){$null}else{(ConvertTo-AdbEndpoint ([string]$Profile.PreferredEndpoint)).Endpoint}
    [pscustomobject][ordered]@{Id=[string]$Profile.Id;Name=(Assert-AdbFriendlyName ([string]$Profile.Name));PreferredEndpoint=$endpoint;ForceUsbOnly=(ConvertTo-AdbBoolean $Profile.ForceUsbOnly 'ForceUsbOnly');LastUsedUtc=ConvertTo-AdbUtcTimestamp ([string]$Profile.LastUsedUtc);Notes=if($null -eq $Profile.Notes){$null}else{[string]$Profile.Notes}}
}

function ConvertTo-AdbStoredMode {
    param([string]$Mode)
    switch ($Mode) {
        'usb'                    { return 'usb' }
        'wireless-debugging-tls' { return 'wireless-debugging-tls' }
        'WirelessTls'            { return 'wireless-debugging-tls' }
        'ModernPairing'          { return 'wireless-debugging-tls' }
        'tcpip-5555'             { return 'tcpip-5555' }
        'LegacyTcpIp'            { return 'tcpip-5555' }
        default { throw "Modo de dispositivo no admitido: $Mode" }
    }
}

function Get-AdbDeviceStoreBackupDirectory {
    [CmdletBinding()]
    param()
    return (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'backups')
}

function Get-AdbDeviceStoreBackups {
    [CmdletBinding()]
    param()

    $directory = Get-AdbDeviceStoreBackupDirectory
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { return @() }
    return @(Get-ChildItem -LiteralPath $directory -Filter 'devices-*.json' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending)
}

function Backup-AdbDeviceStore {
    [CmdletBinding()]
    param([string]$SourcePath = (Get-AdbDeviceStorePath))

    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { return $null }
    $directory = Get-AdbDeviceStoreBackupDirectory
    New-Item -ItemType Directory -Path $directory -Force | Out-Null

    $suffix = 0
    do {
        $stamp = [DateTime]::Now.AddSeconds($suffix).ToString('yyyyMMdd-HHmmss')
        $backupPath = Join-Path $directory ("devices-{0}.json" -f $stamp)
        $suffix++
    } while (Test-Path -LiteralPath $backupPath)

    Copy-Item -LiteralPath $SourcePath -Destination $backupPath -Force -ErrorAction Stop
    $backups = @(Get-AdbDeviceStoreBackups)
    foreach ($oldBackup in ($backups | Select-Object -Skip 20)) {
        Remove-Item -LiteralPath $oldBackup.FullName -Force -ErrorAction Stop
    }
    return $backupPath
}

function Restore-AdbDeviceStore {
    [CmdletBinding()]
    param(
        [string]$BackupPath,
        [ValidateRange(1, 1000)][int]$Index,
        [switch]$List
    )

    $backups = @(Get-AdbDeviceStoreBackups)
    if ($List -or ([string]::IsNullOrWhiteSpace($BackupPath) -and $Index -eq 0)) {
        return @($backups | Select-Object @{Name='Index';Expression={[array]::IndexOf($backups, $_) + 1}}, Name, FullName, Length, LastWriteTimeUtc)
    }
    if ($Index -gt 0) {
        if ($Index -gt $backups.Count) { throw "No existe la copia de seguridad numero $Index." }
        $BackupPath = $backups[$Index - 1].FullName
    }
    if ([string]::IsNullOrWhiteSpace($BackupPath)) { throw 'Indica -BackupPath o -Index para restaurar una copia.' }
    $resolvedBackup = (Resolve-Path -LiteralPath $BackupPath -ErrorAction Stop).Path
    if ($backups.FullName -notcontains $resolvedBackup) { throw 'La ruta no corresponde a una copia de devices.json administrada por el sistema.' }

    try { $restored = Get-Content -LiteralPath $resolvedBackup -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { throw "La copia no contiene JSON valido: $($_.Exception.Message)" }
    if ($null -eq $restored -or $null -eq $restored.PSObject.Properties['Devices']) {
        throw 'La copia no contiene una estructura valida de devices.json.'
    }
    $target = Get-AdbDeviceStorePath
    Save-AdbDeviceStore -Store $restored | Out-Null
    return (Read-AdbDeviceStore)
}

function Save-AdbDeviceStore {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][object]$Store)

    $path = Get-AdbDeviceStorePath
    $directory = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if ($null -eq $Store -or $null -eq $Store.PSObject.Properties['Devices']) {
        throw 'El registro de dispositivos no contiene la propiedad Devices.'
    }
    $devices = @($Store.Devices)
    $document = [ordered]@{
        SchemaVersion = 1
        UpdatedUtc    = [DateTime]::UtcNow.ToString('o')
        CurrentDeviceId = if ($null -ne $Store.PSObject.Properties['CurrentDeviceId']) { $Store.CurrentDeviceId } else { $null }
        Devices       = $devices
    }
    $json = $document | ConvertTo-Json -Depth 8
    $temporaryPath = "{0}.{1}.tmp" -f $path,[guid]::NewGuid().ToString('N')
    try {
        if (Test-Path -LiteralPath $path -PathType Leaf) { Backup-AdbDeviceStore -SourcePath $path | Out-Null }
        [IO.File]::WriteAllText($temporaryPath, $json, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporaryPath -Destination $path -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue }
    }
    return $path
}

function Import-LegacyAdbDeviceConfig {
    param()
    $legacyPath = Get-AndroidToolsConfigPath
    if (-not (Test-Path -LiteralPath $legacyPath)) { return $null }
    try { $legacy = Get-Content -LiteralPath $legacyPath -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { return $null }
    if ([string]::IsNullOrWhiteSpace([string]$legacy.Endpoint)) { return $null }
    try { $endpoint = (ConvertTo-AdbEndpoint ([string]$legacy.Endpoint)).Endpoint }
    catch { return $null }
    try { $mode = ConvertTo-AdbStoredMode ([string]$legacy.Mode) }
    catch { return $null }
    try {
        $lastConnectedUtc = if ($legacy.UpdatedUtc) { ConvertTo-AdbUtcTimestamp ([string]$legacy.UpdatedUtc) } else { $null }
    } catch { $lastConnectedUtc = $null }
    $hostName = (ConvertTo-AdbEndpoint $endpoint).Host
    return [pscustomobject][ordered]@{
        Id               = [guid]::NewGuid().ToString('N')
        FriendlyName     = "Android $hostName"
        UsbSerial        = $null
        LastEndpoint     = $endpoint
        Mode             = $mode
        LastConnectedUtc = $lastConnectedUtc
        IsPaired         = ($mode -eq 'wireless-debugging-tls')
    }
}

function Read-AdbDeviceStore {
    [CmdletBinding()]
    param()

    $path = Get-AdbDeviceStorePath
    if (-not (Test-Path -LiteralPath $path)) {
        $store = New-AdbDeviceStore
        $legacyDevice = Import-LegacyAdbDeviceConfig
        if ($null -ne $legacyDevice) {
            $store.Devices = @($legacyDevice)
        }
        Save-AdbDeviceStore -Store $store | Out-Null
        return $store
    }
    try { $store = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json }
    catch { throw "No se pudo leer '$path': $($_.Exception.Message)" }
    if ($null -eq $store -or $null -eq $store.PSObject.Properties['SchemaVersion']) {
        throw 'devices.json no contiene SchemaVersion.'
    }
    if ([int]$store.SchemaVersion -ne 1) { throw "Version de devices.json no admitida: $($store.SchemaVersion)" }
    if ($null -eq $store.PSObject.Properties['Devices']) { throw 'devices.json no contiene Devices.' }
    $updatedUtc = if ($null -eq $store.PSObject.Properties['UpdatedUtc']) { $null } else { ConvertTo-AdbUtcTimestamp ([string]$store.UpdatedUtc) }
    $normalized = @()
    $seenIds = @{}
    foreach ($device in @($store.Devices)) {
        foreach ($field in @('Id','FriendlyName','UsbSerial','LastEndpoint','Mode','LastConnectedUtc','IsPaired')) {
            if ($null -eq $device.PSObject.Properties[$field]) {
                throw "devices.json contiene un dispositivo sin el campo '$field'."
            }
        }
        $id = [string]$device.Id
        if ([string]::IsNullOrWhiteSpace($id) -or [string]::IsNullOrWhiteSpace([string]$device.FriendlyName)) {
            throw 'devices.json contiene un dispositivo sin Id o FriendlyName.'
        }
        if ($seenIds.ContainsKey($id)) { throw "devices.json contiene el Id duplicado '$id'." }
        $seenIds[$id] = $true
        $mode = ConvertTo-AdbStoredMode ([string]$device.Mode)
        $endpoint = $null
        if (-not [string]::IsNullOrWhiteSpace([string]$device.LastEndpoint)) {
            $endpoint = (ConvertTo-AdbEndpoint ([string]$device.LastEndpoint)).Endpoint
        }
        $networkProfiles=@()
        if($null -ne $device.PSObject.Properties['NetworkProfiles']){foreach($networkProfile in @($device.NetworkProfiles)){$networkProfiles+=ConvertTo-AdbNetworkProfile $networkProfile}}
        $normalized += [pscustomobject][ordered]@{
            Id               = $id
            FriendlyName     = Assert-AdbFriendlyName ([string]$device.FriendlyName)
            UsbSerial        = if ([string]::IsNullOrWhiteSpace([string]$device.UsbSerial)) { $null } else { [string]$device.UsbSerial }
            LastEndpoint     = $endpoint
            Mode             = $mode
            LastConnectedUtc = ConvertTo-AdbUtcTimestamp ([string]$device.LastConnectedUtc)
            IsPaired         = ConvertTo-AdbBoolean $device.IsPaired 'IsPaired'
            NetworkProfiles  = @($networkProfiles)
        }
    }
    $currentDeviceId = if ($null -eq $store.PSObject.Properties['CurrentDeviceId']) { $null } else { [string]$store.CurrentDeviceId }
    return [pscustomobject][ordered]@{ SchemaVersion=1; UpdatedUtc=$updatedUtc; CurrentDeviceId=$currentDeviceId; Devices=@($normalized) }
}

function Get-SavedAdbDevices {
    [CmdletBinding()]
    param()
    return @((Read-AdbDeviceStore).Devices)
}

function Add-SavedAdbDevice {
    [CmdletBinding()]
    param(
        [string]$FriendlyName,
        [Parameter(Mandatory = $true)][string]$Mode,
        [string]$UsbSerial,
        [string]$LastEndpoint,
        [bool]$IsPaired = $false,
        [string]$LastConnectedUtc,
        [object[]]$NetworkProfiles = @()
    )

    $store = Read-AdbDeviceStore
    if ([string]::IsNullOrWhiteSpace($FriendlyName)) {
        $FriendlyName = if (-not [string]::IsNullOrWhiteSpace($UsbSerial)) { $UsbSerial } elseif (-not [string]::IsNullOrWhiteSpace($LastEndpoint)) { $LastEndpoint } else { 'Dispositivo Android' }
    }
    $name = Assert-AdbFriendlyName $FriendlyName
    $storedMode = ConvertTo-AdbStoredMode $Mode
    $endpoint = if ([string]::IsNullOrWhiteSpace($LastEndpoint)) { $null } else { (ConvertTo-AdbEndpoint $LastEndpoint).Endpoint }
    $usb = if ([string]::IsNullOrWhiteSpace($UsbSerial)) { $null } else { $UsbSerial.Trim() }
    $duplicate = @($store.Devices | Where-Object {
        ($usb -and $_.UsbSerial -ceq $usb) -or ($endpoint -and $_.LastEndpoint -ieq $endpoint)
    })[0]
    if ($null -ne $duplicate) { throw "El dispositivo ya existe: $($duplicate.FriendlyName) [$($duplicate.Id)]" }
    $device = [pscustomobject][ordered]@{
        Id               = [guid]::NewGuid().ToString('N')
        FriendlyName     = $name
        UsbSerial        = $usb
        LastEndpoint     = $endpoint
        Mode             = $storedMode
        LastConnectedUtc = ConvertTo-AdbUtcTimestamp $LastConnectedUtc
        IsPaired         = $IsPaired
        NetworkProfiles  = @($NetworkProfiles | ForEach-Object { ConvertTo-AdbNetworkProfile $_ })
    }
    $store.Devices = @($store.Devices) + $device
    Save-AdbDeviceStore -Store $store | Out-Null
    return $device
}

function Update-SavedAdbDevice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [string]$FriendlyName,
        [string]$UsbSerial,
        [string]$LastEndpoint,
        [string]$Mode,
        [Nullable[bool]]$IsPaired,
        [string]$LastConnectedUtc
    )

    $store = Read-AdbDeviceStore
    $device = @($store.Devices | Where-Object { $_.Id -ceq $Id })[0]
    if ($null -eq $device) { throw "No existe un dispositivo con Id '$Id'." }
    if ($PSBoundParameters.ContainsKey('FriendlyName')) { $device.FriendlyName = Assert-AdbFriendlyName $FriendlyName }
    if ($PSBoundParameters.ContainsKey('UsbSerial')) { $device.UsbSerial = if ([string]::IsNullOrWhiteSpace($UsbSerial)) { $null } else { $UsbSerial.Trim() } }
    if ($PSBoundParameters.ContainsKey('LastEndpoint')) { $device.LastEndpoint = if ([string]::IsNullOrWhiteSpace($LastEndpoint)) { $null } else { (ConvertTo-AdbEndpoint $LastEndpoint).Endpoint } }
    if ($PSBoundParameters.ContainsKey('Mode')) { $device.Mode = ConvertTo-AdbStoredMode $Mode }
    if ($PSBoundParameters.ContainsKey('IsPaired')) { $device.IsPaired = [bool]$IsPaired }
    if ($PSBoundParameters.ContainsKey('LastConnectedUtc')) { $device.LastConnectedUtc = ConvertTo-AdbUtcTimestamp $LastConnectedUtc }
    $conflict = @($store.Devices | Where-Object {
        $_.Id -cne $device.Id -and (
            ($device.UsbSerial -and $_.UsbSerial -ceq $device.UsbSerial) -or
            ($device.LastEndpoint -and $_.LastEndpoint -ieq $device.LastEndpoint)
        )
    })[0]
    if ($null -ne $conflict) { throw "La actualizacion coincide con '$($conflict.FriendlyName)' [$($conflict.Id)]." }
    Save-AdbDeviceStore -Store $store | Out-Null
    return $device
}

function Remove-SavedAdbDevice {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Id)
    $store = Read-AdbDeviceStore
    $before = @($store.Devices).Count
    $store.Devices = @($store.Devices | Where-Object { $_.Id -cne $Id })
    if (@($store.Devices).Count -eq $before) { throw "No existe un dispositivo con Id '$Id'." }
    Save-AdbDeviceStore -Store $store | Out-Null
    return $true
}

function Get-SavedAdbNetworkProfiles {
    [CmdletBinding()] param([Parameter(Mandatory=$true)][string]$DeviceId)
    $device=@((Read-AdbDeviceStore).Devices|Where-Object Id -ceq $DeviceId)[0]
    if($null -eq $device){throw "No existe un dispositivo con Id '$DeviceId'."}
    return @($device.NetworkProfiles)
}

function Add-SavedAdbNetworkProfile {
    [CmdletBinding()] param([Parameter(Mandatory=$true)][string]$DeviceId,[Parameter(Mandatory=$true)][string]$Name,[string]$PreferredEndpoint,[bool]$ForceUsbOnly=$false,[string]$LastUsedUtc,[string]$Notes)
    $store=Read-AdbDeviceStore;$device=@($store.Devices|Where-Object Id -ceq $DeviceId)[0];if($null -eq $device){throw "No existe un dispositivo con Id '$DeviceId'."};$safeName=Assert-AdbFriendlyName $Name;if(@($device.NetworkProfiles|Where-Object Name -ceq $safeName).Count){throw "Ya existe el perfil '$safeName' en el dispositivo."};$profile=[pscustomobject][ordered]@{Id=[guid]::NewGuid().ToString('N');Name=$safeName;PreferredEndpoint=if([string]::IsNullOrWhiteSpace($PreferredEndpoint)){$null}else{(ConvertTo-AdbEndpoint $PreferredEndpoint).Endpoint};ForceUsbOnly=$ForceUsbOnly;LastUsedUtc=ConvertTo-AdbUtcTimestamp $LastUsedUtc;Notes=if($null -eq $Notes){$null}else{$Notes}};$device.NetworkProfiles=@($device.NetworkProfiles)+$profile;Save-AdbDeviceStore $store|Out-Null;return $profile
}

function Update-SavedAdbNetworkProfile {
    [CmdletBinding()] param([Parameter(Mandatory=$true)][string]$DeviceId,[Parameter(Mandatory=$true)][string]$ProfileId,[string]$Name,[string]$PreferredEndpoint,[Nullable[bool]]$ForceUsbOnly,[string]$LastUsedUtc,[string]$Notes)
    $store=Read-AdbDeviceStore;$device=@($store.Devices|Where-Object Id -ceq $DeviceId)[0];if($null -eq $device){throw "No existe un dispositivo con Id '$DeviceId'."};$profile=@($device.NetworkProfiles|Where-Object Id -ceq $ProfileId)[0];if($null -eq $profile){throw "No existe el perfil '$ProfileId'."};if($PSBoundParameters.ContainsKey('Name')){$profile.Name=Assert-AdbFriendlyName $Name};if($PSBoundParameters.ContainsKey('PreferredEndpoint')){$profile.PreferredEndpoint=if([string]::IsNullOrWhiteSpace($PreferredEndpoint)){$null}else{(ConvertTo-AdbEndpoint $PreferredEndpoint).Endpoint}};if($PSBoundParameters.ContainsKey('ForceUsbOnly')){$profile.ForceUsbOnly=[bool]$ForceUsbOnly};if($PSBoundParameters.ContainsKey('LastUsedUtc')){$profile.LastUsedUtc=ConvertTo-AdbUtcTimestamp $LastUsedUtc};if($PSBoundParameters.ContainsKey('Notes')){$profile.Notes=$Notes};Save-AdbDeviceStore $store|Out-Null;return $profile
}

function Remove-SavedAdbNetworkProfile {
    [CmdletBinding()] param([Parameter(Mandatory=$true)][string]$DeviceId,[Parameter(Mandatory=$true)][string]$ProfileId)
    $store=Read-AdbDeviceStore;$device=@($store.Devices|Where-Object Id -ceq $DeviceId)[0];if($null -eq $device){throw "No existe un dispositivo con Id '$DeviceId'."};$before=@($device.NetworkProfiles).Count;$device.NetworkProfiles=@($device.NetworkProfiles|Where-Object Id -cne $ProfileId);if($device.NetworkProfiles.Count -eq $before){throw "No existe el perfil '$ProfileId'."};Save-AdbDeviceStore $store|Out-Null;return $true
}

function Get-AdbMdnsServices {
    [CmdletBinding()]
    param()
    $services = @()
    foreach ($line in @(Invoke-AdbHost -Arguments @('mdns','services'))) {
        $text = [string]$line
        if ($text -notmatch 'adb-tls-connect') { continue }
        $endpointMatch = [regex]::Match($text, '(?<endpoint>\[[^\]]+\]:\d{1,5}|[A-Za-z0-9._-]+:\d{1,5})')
        if (-not $endpointMatch.Success) { continue }
        try { $parts = ConvertTo-AdbEndpoint $endpointMatch.Groups['endpoint'].Value } catch { continue }
        $tokens = $text.Trim() -split '\s+'
        $services += [pscustomobject]@{
            ServiceName = if ($tokens.Count -gt 0) { $tokens[0] } else { $null }
            Type        = 'adb-tls-connect'
            Host        = $parts.Host
            Port        = $parts.Port
            Endpoint    = $parts.Endpoint
            Raw         = $text.Trim()
        }
    }
    return @($services)
}

function Get-AdbDeviceIpv4 {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Serial)
    foreach ($arguments in @(@('shell','ip','route'), @('shell','ip','addr'))) {
        foreach ($line in @(Invoke-Adb -Serial $Serial -Arguments $arguments)) {
            $match = [regex]::Match([string]$line, '(?:\bsrc\s+|\binet\s+)(?<ip>\d{1,3}(?:\.\d{1,3}){3})(?:/\d+)?')
            if ($match.Success -and $match.Groups['ip'].Value -notlike '127.*') { return $match.Groups['ip'].Value }
        }
    }
    return $null
}

function Get-AdbSyncProfilesPath {
    return (Join-Path (Join-Path $env:USERPROFILE 'AndroidTools') 'sync-profiles.json')
}

function New-AdbSyncProfileStore {
    return [pscustomobject][ordered]@{ SchemaVersion=1; UpdatedUtc=[DateTime]::UtcNow.ToString('o'); Profiles=@() }
}

function Read-AdbSyncProfiles {
    $path = Get-AdbSyncProfilesPath
    if (-not (Test-Path -LiteralPath $path)) { return (New-AdbSyncProfileStore) }
    try { $store = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json } catch { throw "No se pudo leer sync-profiles.json: $($_.Exception.Message)" }
    if ($null -eq $store.PSObject.Properties['Profiles']) { throw 'sync-profiles.json no contiene Profiles.' }
    return $store
}

function Save-AdbSyncProfiles {
    param([Parameter(Mandatory=$true)][object]$Store)
    $path = Get-AdbSyncProfilesPath; $dir = Split-Path -Parent $path
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $document = [ordered]@{ SchemaVersion=1; UpdatedUtc=[DateTime]::UtcNow.ToString('o'); Profiles=@($Store.Profiles) }
    $tmp = "$path.$([guid]::NewGuid().ToString('N')).tmp"
    try { [IO.File]::WriteAllText($tmp,($document | ConvertTo-Json -Depth 10),(New-Object Text.UTF8Encoding($false))); Move-Item -LiteralPath $tmp -Destination $path -Force }
    finally { if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue} }
    return $path
}

function Get-AdbSyncProfiles { return @((Read-AdbSyncProfiles).Profiles) }

function Add-AdbSyncProfile {
    param([Parameter(Mandatory=$true)][string]$Name,[Parameter(Mandatory=$true)][string[]]$SourcePaths,[Parameter(Mandatory=$true)][string]$DestinationPath,[string[]]$Extensions,[Nullable[int]]$ModifiedWithinDays,[Nullable[long]]$MinSizeBytes,[Nullable[long]]$MaxSizeBytes,[bool]$Overwrite=$false)
    $store=Read-AdbSyncProfiles; $safeName=Assert-AdbFriendlyName $Name; if(@($store.Profiles | Where-Object Name -ceq $safeName).Count){throw "Ya existe el perfil '$safeName'."}
    $sources=@($SourcePaths | ForEach-Object { ConvertTo-SafeAndroidPath $_ }); if(-not $sources.Count){throw 'Debe existir al menos una ruta de origen.'}
    $profile=[pscustomobject][ordered]@{Id=[guid]::NewGuid().ToString('N');Name=$safeName;SourcePaths=$sources;Filters=[pscustomobject][ordered]@{Extensions=@($Extensions);ModifiedWithinDays=$ModifiedWithinDays;MinSizeBytes=$MinSizeBytes;MaxSizeBytes=$MaxSizeBytes};DestinationPath=[IO.Path]::GetFullPath($DestinationPath);Overwrite=$Overwrite}
    $store.Profiles=@($store.Profiles)+$profile; Save-AdbSyncProfiles $store|Out-Null; return $profile
}

function Update-AdbSyncProfile {
    param([Parameter(Mandatory=$true)][string]$Id,[string]$Name,[string[]]$SourcePaths,[string]$DestinationPath,[string[]]$Extensions,[Nullable[int]]$ModifiedWithinDays,[Nullable[long]]$MinSizeBytes,[Nullable[long]]$MaxSizeBytes,[Nullable[bool]]$Overwrite)
    $store=Read-AdbSyncProfiles; $p=@($store.Profiles|Where-Object Id -ceq $Id)[0]; if($null -eq $p){throw "No existe el perfil '$Id'."}
    if($PSBoundParameters.ContainsKey('Name')){$p.Name=Assert-AdbFriendlyName $Name}; if($PSBoundParameters.ContainsKey('SourcePaths')){$p.SourcePaths=@($SourcePaths|ForEach-Object{ConvertTo-SafeAndroidPath $_})}; if($PSBoundParameters.ContainsKey('DestinationPath')){$p.DestinationPath=[IO.Path]::GetFullPath($DestinationPath)}; if(-not $p.PSObject.Properties['Filters']){$p|Add-Member Filters ([pscustomobject]@{})}; foreach($n in @('Extensions','ModifiedWithinDays','MinSizeBytes','MaxSizeBytes')){if($PSBoundParameters.ContainsKey($n)){$p.Filters.$n=Get-Variable $n -ValueOnly}}; if($PSBoundParameters.ContainsKey('Overwrite')){$p.Overwrite=[bool]$Overwrite}; $store.Profiles=@($store.Profiles); Save-AdbSyncProfiles $store|Out-Null; return $p
}

function Remove-AdbSyncProfile { param([Parameter(Mandatory=$true)][string]$Id) $store=Read-AdbSyncProfiles; $before=@($store.Profiles).Count; $store.Profiles=@($store.Profiles|Where-Object Id -cne $Id); if($store.Profiles.Count -eq $before){throw "No existe el perfil '$Id'."}; Save-AdbSyncProfiles $store|Out-Null; return $true }

function Invoke-AdbSyncProfile {
    [CmdletBinding()] param([Parameter(Mandatory=$true)][object]$Profile,[object]$Device)
    $selected=if($PSBoundParameters.ContainsKey('Device')){Set-CurrentAdbDevice $Device}else{Get-CurrentAdbDevice}; if($null -eq $selected -or -not(Test-AdbDeviceReady $selected $true)){throw 'El dispositivo no esta listo para sincronizar.'}; $serial=Resolve-AdbSerial (Get-CurrentAdbDevice)
    $filters=$Profile.Filters; $extensions=@($filters.Extensions|Where-Object{$_}|ForEach-Object{([string]$_).ToLowerInvariant()}); $cutoff=if($filters.ModifiedWithinDays){[DateTimeOffset]::UtcNow.AddDays(-[int]$filters.ModifiedWithinDays).ToUnixTimeSeconds()}else{$null}; $summary=[ordered]@{Profile=$Profile.Name;Copied=0;Skipped=0;Errors=0;Bytes=0;ErrorDetails=@()}; New-Item -ItemType Directory -Path $Profile.DestinationPath -Force|Out-Null
    foreach($root in @($Profile.SourcePaths)){try{$files=@(Invoke-AdbCommand $serial @('shell','find',$root,'-type','f','-print'));foreach($line in $files){$remote=([string]$line).Trim();if([string]::IsNullOrWhiteSpace($remote)){continue};$stat=@(Invoke-AdbCommand $serial @('shell','stat','-c','%s|%Y|%n',$remote));$parts=([string]($stat -join '')).Split('|',3);if($parts.Count -lt 3){$summary.Errors++;continue};$size=[long]$parts[0];$mtime=[long]$parts[1];$name=$parts[2];$ext=[IO.Path]::GetExtension($name).ToLowerInvariant();if($extensions.Count -and $extensions -notcontains $ext){continue};if($null -ne $cutoff -and $mtime -lt $cutoff){continue};if($null -ne $filters.MinSizeBytes -and $size -lt [long]$filters.MinSizeBytes){continue};if($null -ne $filters.MaxSizeBytes -and $size -gt [long]$filters.MaxSizeBytes){continue};$relative=$remote.Substring($root.Length).TrimStart('/');$local=Join-Path $Profile.DestinationPath ($relative -replace '/','\');New-Item -ItemType Directory -Path (Split-Path -Parent $local) -Force|Out-Null;if((Test-Path -LiteralPath $local)-and -not $Profile.Overwrite){$summary.Skipped++;continue};try{Invoke-AdbCommand $serial @('pull',$remote,$local)|Out-Null;if(-not(Test-Path -LiteralPath $local)){throw 'adb pull no creo el archivo local.'};$summary.Copied++;$summary.Bytes+=$size}catch{$summary.Errors++;$summary.ErrorDetails+=('{0}: {1}'-f $remote,$_.Exception.Message)}}}catch{$summary.Errors++;$summary.ErrorDetails+=('{0}: {1}'-f $root,$_.Exception.Message)}}
    $level=if($summary.Errors){'ERROR'}else{'SUCCESS'};Write-AdbLog -Level $level -Source 'AndroidAdb.Invoke-AdbSyncProfile' -Serial $serial -Message ("Perfil {0}: copiados={1}, omitidos={2}, errores={3}, bytes={4}"-f $Profile.Name,$summary.Copied,$summary.Skipped,$summary.Errors,$summary.Bytes);return [pscustomobject]$summary
}

function Invoke-AdbDiagnostics {
    [CmdletBinding()]
    param([switch]$NoPause)
    Initialize-AdbLog | Out-Null
    $result=[ordered]@{Recommendations=@();AdbPath=$null;AdbVersion=$null;Devices=@();Mdns=@();SavedCount=0;Selected=$null;LocalFreeBytes=$null;PhoneFreeBytes=$null;LogErrors=@()}
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '                 DIAGNOSTICO ANDROID ADB' -ForegroundColor Yellow
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host "`n[1] ADB" -ForegroundColor Cyan
    $adb=Get-Command adb -ErrorAction SilentlyContinue
    if($null -eq $adb){Write-Host 'ERROR: adb no esta disponible en PATH.' -ForegroundColor Red;$result.Recommendations+='Instala Android Platform-Tools y agrega adb al PATH.'}else{$result.AdbPath=$adb.Source;try{$result.AdbVersion=@(Invoke-AdbHost -Arguments @('version'));Write-Host ($result.AdbVersion -join ' ') -ForegroundColor Green;Write-Host ("Ruta: {0}"-f $adb.Source);$versionText=$result.AdbVersion -join ' ';if($versionText -match 'Android Debug Bridge version 1\.0\.(\d+)' -and [int]$Matches[1] -lt 41){$result.Recommendations+='Actualiza Android SDK Platform-Tools: la version de ADB parece antigua.'}}catch{Write-Host $_ -ForegroundColor Red;$result.Recommendations+='No se pudo ejecutar adb version.'}}
    Write-Host "`n[2] Dispositivos adb devices -l" -ForegroundColor Cyan
    try{$result.Devices=@(Get-AdbDevices);if($result.Devices.Count){$result.Devices|Format-Table Serial,Status,TransportId -AutoSize}else{Write-Host 'No hay dispositivos detectados.' -ForegroundColor Yellow;$result.Recommendations+='Conecta o reconecta un dispositivo y verifica la autorizacion ADB.'}}catch{Write-Host ("ERROR: {0}"-f $_.Exception.Message)-ForegroundColor Red;$result.Recommendations+='No se pudo consultar adb devices.'}
    Write-Host "`n[3] mDNS" -ForegroundColor Cyan
    try{$result.Mdns=@(Get-AdbMdnsServices);if($result.Mdns.Count){$result.Mdns|Format-Table ServiceName,Endpoint -AutoSize}else{Write-Host 'No se encontraron servicios mDNS.' -ForegroundColor Yellow}}catch{Write-Host ("ERROR: {0}"-f $_.Exception.Message)-ForegroundColor Red}
    Write-Host "`n[4] devices.json" -ForegroundColor Cyan
    try{$saved=@(Get-SavedAdbDevices);$result.SavedCount=$saved.Count;Write-Host ("Dispositivos guardados: {0}"-f $saved.Count);$saved|Sort-Object LastConnectedUtc -Descending|Select-Object -First 5 FriendlyName,Mode,LastEndpoint,LastConnectedUtc|Format-Table -AutoSize}catch{Write-Host ("ERROR: {0}"-f $_.Exception.Message)-ForegroundColor Red;$result.Recommendations+='Revisa la integridad de devices.json.'}
    Write-Host "`n[5] Seleccion actual" -ForegroundColor Cyan
    try{$selected=Get-CurrentAdbDevice;if($null -eq $selected){Write-Host 'No hay dispositivo seleccionado.' -ForegroundColor Yellow;$result.Recommendations+='Ejecuta reconexion y selecciona un dispositivo.'}else{$serial=Resolve-AdbSerial (Get-CurrentAdbDevice);$record=@($result.Devices|Where-Object Serial -ceq $serial)[0];$state=if($record){$record.Status}else{'no encontrado'};$result.Selected=[pscustomobject]@{Name=$selected.FriendlyName;Serial=$serial;Endpoint=$selected.LastEndpoint;State=$state};Write-Host ("{0} | {1} | Estado: {2}"-f $selected.FriendlyName,$serial,$state) -ForegroundColor ($(if($state -ceq 'device'){'Green'}else{'Red'}));if($state -cne 'device'){$result.Recommendations+='El dispositivo seleccionado no esta en estado device; reconectalo antes de operar.'}}}catch{Write-Host $_ -ForegroundColor Red}
    Write-Host "`n[6] Ultimos 10 errores del log" -ForegroundColor Cyan
    try{$log=Get-AdbLogPath;$result.LogErrors=@(if(Test-Path -LiteralPath $log){Get-Content -LiteralPath $log|Where-Object{$_ -match '\[ERROR\]'}|Select-Object -Last 10});if($result.LogErrors.Count){$result.LogErrors|ForEach-Object{Write-Host $_ -ForegroundColor Red}}else{Write-Host 'No hay errores registrados.' -ForegroundColor Green}}catch{Write-Host 'No se pudo leer el log.' -ForegroundColor Yellow}
    Write-Host "`n[7] Espacio" -ForegroundColor Cyan
    try{$drive=Get-PSDrive -Name ([IO.Path]::GetPathRoot((Get-Location).Path).TrimEnd('\').Substring(0,1));$result.LocalFreeBytes=$drive.Free;Write-Host ("PC: {0:N2} GB libres"-f ($drive.Free/1GB))}catch{Write-Host 'No se pudo consultar el disco local.' -ForegroundColor Yellow}
    if($result.Selected -and $result.Selected.State -ceq 'device'){try{$df=@(Invoke-AdbCommand -Serial $result.Selected.Serial -Arguments @('shell','df','-k','/sdcard'));$line=@($df|Where-Object{[string]$_ -match '/sdcard\s*$'})[0];if($line -match '^\s*\S+\s+(\d+)\s+(\d+)\s+(\d+)'){ $result.PhoneFreeBytes=[long]$Matches[3]*1KB;Write-Host ("Telefono /sdcard: {0:N2} GB libres"-f ($result.PhoneFreeBytes/1GB))}}catch{Write-Host ("No se pudo consultar almacenamiento del telefono: {0}"-f $_.Exception.Message)-ForegroundColor Yellow}}
    if($result.Recommendations.Count){Write-Host "`n[8] Recomendaciones" -ForegroundColor Cyan;$result.Recommendations|Select-Object -Unique|ForEach-Object{Write-Host ("- {0}"-f $_) -ForegroundColor Yellow}}
    else{Write-Host "`n[8] Recomendaciones`n- No se detectaron problemas basicos." -ForegroundColor Green}
    Write-AdbLog -Level INFO -Source 'AndroidAdb.Invoke-AdbDiagnostics' -Message ("Diagnostico: dispositivos={0}, guardados={1}, recomendaciones={2}"-f $result.Devices.Count,$result.SavedCount,$result.Recommendations.Count)
    if(-not $NoPause){Read-Host "`nPresiona Enter para continuar"|Out-Null};return [pscustomobject]$result
}

Export-ModuleMember -Function Initialize-AdbLog, Write-AdbLog, Get-AdbLogPath, Get-AdbDevices, Get-SingleReadyDevice, Get-CurrentAdbDevice, Set-CurrentAdbDevice, Clear-CurrentAdbDevice, Select-CurrentAdbDevice, Test-AdbDeviceReady, Resolve-AdbSerial, Invoke-AdbCommand, Invoke-Adb, Invoke-AdbHost, Test-AdbAvailable, ConvertTo-AdbEndpoint, Get-AndroidToolsConfigPath, Save-AdbConnectionConfig, Read-AdbConnectionConfig, Get-AdbDeviceRecord, Wait-AdbDevice, Connect-AdbEndpoint, Get-AdbMdnsConnectionEndpoint, Get-AdbMdnsServices, Get-AdbDeviceIpv4, Get-AdbDeviceStorePath, New-AdbDeviceStore, Read-AdbDeviceStore, Save-AdbDeviceStore, Get-SavedAdbDevices, Add-SavedAdbDevice, Update-SavedAdbDevice, Remove-SavedAdbDevice, Get-SavedAdbNetworkProfiles, Add-SavedAdbNetworkProfile, Update-SavedAdbNetworkProfile, Remove-SavedAdbNetworkProfile, Get-AdbDeviceStoreBackupDirectory, Get-AdbDeviceStoreBackups, Backup-AdbDeviceStore, Restore-AdbDeviceStore, Capture-AdbScreenshot, Start-AdbScreenRecording, Test-ScrcpyAvailable, Start-AdbScrcpy, Get-AdbSyncProfilesPath, New-AdbSyncProfileStore, Read-AdbSyncProfiles, Save-AdbSyncProfiles, Get-AdbSyncProfiles, Add-AdbSyncProfile, Update-AdbSyncProfile, Remove-AdbSyncProfile, Invoke-AdbSyncProfile, Invoke-AdbDiagnostics, ConvertTo-SafeAdbSearchPattern, ConvertTo-SafeAndroidPath, ConvertTo-SafeWindowsName, Protect-BackupDirectory
