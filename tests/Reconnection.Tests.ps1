$root = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $root 'AndroidAdb.psm1') -Force
. (Join-Path $root 'android_reconnect.ps1') -NoMenu

Describe 'Reconexión PowerShell' {
    It 'prueba un endpoint guardado sin BSSID' {
        Test-EndpointCurrentNetwork ([pscustomobject]@{ Endpoint = '192.168.1.50:41000' }) | Should Be $true
    }

    It 'usa el host si el nombre mDNS guardado ya no existe' {
        Mock -ModuleName AndroidAdb Invoke-AdbHost {
            'adb-phone._adb-tls-connect._tcp _adb-tls-connect._tcp 192.168.1.50:42000'
        }
        $found = Get-AdbMdnsConnectionEndpoint -PreferredHost '192.168.1.50' -PreferredServiceName 'androidtools-old-pairing'
        $found.Endpoint | Should Be '192.168.1.50:42000'
    }
}
