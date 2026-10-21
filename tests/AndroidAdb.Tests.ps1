# =============================================================================
# PRUEBAS UNITARIAS: AndroidAdb.psm1
# =============================================================================

$modulePath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\AndroidAdb.psm1"
$modulePath = [System.IO.Path]::GetFullPath($modulePath)
Import-Module $modulePath -Force

Describe "AndroidAdb Module Unit Tests" {

    Context "ConvertTo-AdbEndpoint" {
        It "Normaliza un endpoint valido con IP y Puerto" {
            $epObj = ConvertTo-AdbEndpoint "192.168.1.100:5555"
            $epObj.Endpoint | Should Be "192.168.1.100:5555"
            $epObj.Host | Should Be "192.168.1.100"
            $epObj.Port | Should Be 5555
        }

        It "Lanza una excepcion cuando falta el puerto o el formato es invalido" {
            { ConvertTo-AdbEndpoint "192.168.1.100" } | Should Throw
        }

        It "Lanza una excepcion cuando el parametro de entrada esta vacio o es nulo" {
            { ConvertTo-AdbEndpoint "" } | Should Throw
        }
    }

    Context "Resolve-AdbSerial" {
        It "Resuelve una cadena de texto endpoint valida como Serial" {
            $res = Resolve-AdbSerial -DeviceOrEndpoint "192.168.1.50:5555"
            $res | Should Be "192.168.1.50:5555"
        }

        It "Extrae el Serial de un objeto de dispositivo estructurado" {
            $devObj = [pscustomobject]@{ Serial = "ABC12345"; FriendlyName = "Mi Telefono" }
            $res = Resolve-AdbSerial -DeviceOrEndpoint $devObj
            $res | Should Be "ABC12345"
        }

        It "Lanza una excepcion cuando se pasa un valor nulo" {
            { Resolve-AdbSerial -DeviceOrEndpoint $null } | Should Throw
        }
    }

    Context "Gestion y Guardado de Dispositivos (Add-SavedAdbDevice & Store)" {
        It "Guarda, consulta, actualiza y elimina un dispositivo ADB en el almacen de datos" {
            $testEndpoint = "192.168.1.199:5555"
            $testName = "Telefono de Pruebas Unitarias"

            # Limpieza previa si existia
            $existing = @(Get-SavedAdbDevices | Where-Object { $_.LastEndpoint -ieq $testEndpoint })
            foreach ($ex in $existing) { Remove-SavedAdbDevice -Id $ex.Id | Out-Null }

            # 1. Guardar
            $dev = Add-SavedAdbDevice -FriendlyName $testName -Mode 'tcpip-5555' -LastEndpoint $testEndpoint
            $dev | Should Not BeNullOrEmpty
            $dev.FriendlyName | Should Be $testName
            $dev.LastEndpoint | Should Be $testEndpoint
            $createdId = $dev.Id

            # 2. Consultar
            $savedList = Get-SavedAdbDevices
            $found = @($savedList | Where-Object { $_.Id -ceq $createdId })[0]
            $found | Should Not BeNullOrEmpty
            $found.FriendlyName | Should Be $testName

            # 3. Actualizar
            $updatedName = "Telefono de Pruebas Modificado"
            $updated = Update-SavedAdbDevice -Id $createdId -FriendlyName $updatedName
            $updated.FriendlyName | Should Be $updatedName

            # 4. Eliminar
            Remove-SavedAdbDevice -Id $createdId | Out-Null
            $afterDelete = @(Get-SavedAdbDevices | Where-Object { $_.Id -ceq $createdId })
            $afterDelete.Count | Should Be 0
        }

        It "Lanza excepcion al intentar agregar un dispositivo duplicado con el mismo Endpoint" {
            $dupEndpoint = "192.168.1.198:5555"
            $existing = @(Get-SavedAdbDevices | Where-Object { $_.LastEndpoint -ieq $dupEndpoint })
            foreach ($ex in $existing) { Remove-SavedAdbDevice -Id $ex.Id | Out-Null }

            $dev = Add-SavedAdbDevice -FriendlyName "Original" -Mode 'tcpip-5555' -LastEndpoint $dupEndpoint
            try {
                { Add-SavedAdbDevice -FriendlyName "Duplicado" -Mode 'tcpip-5555' -LastEndpoint $dupEndpoint } | Should Throw
            } finally {
                Remove-SavedAdbDevice -Id $dev.Id | Out-Null
            }
        }
    }

    Context "Seleccion de Dispositivo Actual (Set-CurrentAdbDevice & Get/Clear)" {
        BeforeAll {
            Mock -ModuleName AndroidAdb Get-AdbDevices {
                return @([pscustomobject]@{ Serial = '192.168.1.80:5555'; Status = 'device'; Raw = 'device' })
            }
        }

        It "Permite seleccionar un dispositivo activo con Set-CurrentAdbDevice" {
            $mockDev = [pscustomobject]@{
                Id           = "MOCK_ID_123"
                FriendlyName = "Pixel Test Device"
                Serial       = "192.168.1.80:5555"
                LastEndpoint = "192.168.1.80:5555"
                Mode         = "tcpip-5555"
            }

            $setResult = Set-CurrentAdbDevice -Device $mockDev
            $setResult.FriendlyName | Should Be "Pixel Test Device"
            $setResult.Serial | Should Be "192.168.1.80:5555"
        }

        It "Obtiene el dispositivo activo correctamente con Get-CurrentAdbDevice" {
            $cur = Get-CurrentAdbDevice
            $cur | Should Not BeNullOrEmpty
            $cur.FriendlyName | Should Be "Pixel Test Device"
            $cur.Serial | Should Be "192.168.1.80:5555"
        }

        It "Limpia la seleccion del dispositivo con Clear-CurrentAdbDevice" {
            Clear-CurrentAdbDevice
            $cur = Get-CurrentAdbDevice
            $cur | Should BeNullOrEmpty
        }
    }

    Context "Sanitizacion de Rutas y Patrones" {
        It "Valida rutas permitidas en ConvertTo-SafeAndroidPath" {
            $safe = ConvertTo-SafeAndroidPath -Path "/sdcard/DCIM"
            $safe | Should Be "/sdcard/DCIM"
        }

        It "Lanza excepcion ante intentos de path traversal en ConvertTo-SafeAndroidPath" {
            { ConvertTo-SafeAndroidPath -Path "/sdcard/../etc/passwd" } | Should Throw
        }

        It "Sanitiza nombres de archivo para Windows en ConvertTo-SafeWindowsName" {
            $safe = ConvertTo-SafeWindowsName -Name "mi_archivo_foto"
            $safe | Should Be "mi_archivo_foto"
        }

        It "Valida patron de busqueda en ConvertTo-SafeAdbSearchPattern" {
            $pattern = ConvertTo-SafeAdbSearchPattern -Value "foto*.jpg"
            $pattern | Should Be "foto*.jpg"
        }
    }

    Context "Sistema de Registro de Logs" {
        It "Inicializa y genera el archivo de log correctamente" {
            $logPath = Initialize-AdbLog
            (Test-Path -LiteralPath $logPath) | Should Be $true
        }

        It "Escribe entradas de log con niveles de severidad validos" {
            Write-AdbLog -Level 'INFO' -Source 'UnitTest' -Message 'Mensaje de prueba automatizada'
            $logPath = Get-AdbLogPath
            $content = Get-Content -LiteralPath $logPath -Raw
            $content | Should Match 'Mensaje de prueba automatizada'
        }
    }

    Context "Historial compartido de endpoints" {
        It "Conserva diez endpoints, el serial USB y el ultimo endpoint" {
            $device = Add-SavedAdbDevice -FriendlyName ('Historial-' + [guid]::NewGuid().ToString('N')) -Mode 'tcpip-5555' -UsbSerial ('USB-' + [guid]::NewGuid().ToString('N'))
            try {
                for ($index = 1; $index -le 12; $index++) {
                    Add-AdbEndpointHistory -DeviceId $device.Id -Endpoint ("192.168.1.{0}:5555" -f $index) | Out-Null
                }
                $saved = @(Get-SavedAdbDevices | Where-Object { $_.Id -ceq $device.Id })[0]
                @($saved.Endpoints).Count | Should Be 10
                $saved.LastEndpoint | Should Be '192.168.1.12:5555'
                $saved.UsbSerial | Should Be $device.UsbSerial
            } finally {
                Remove-SavedAdbDevice -Id $device.Id | Out-Null
            }
        }
    }

    Context "Identidad fisica compartida con la GUI" {
        It "Calcula la misma huella SHA256 normalizada que Python" {
            $value = Get-AdbPhysicalIdHash -PhysicalId ' PHONE-ID '
            $sha = [Security.Cryptography.SHA256]::Create()
            try {
                $expected = 'sha256:' + ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes('phone-id'))).Replace('-','').ToLowerInvariant())
            } finally { $sha.Dispose() }
            $value | Should Be $expected
        }
    }

    Context "Exportacion de Funciones del Modulo" {
        It "Exporta las funciones criticas requeridas por la interfaz" {
            $mod = Get-Module AndroidAdb
            $exported = $mod.ExportedFunctions.Keys
            
            ($exported -contains "Invoke-AdbCommand") | Should Be $true
            ($exported -contains "Get-AdbDevices") | Should Be $true
            ($exported -contains "Get-CurrentAdbDevice") | Should Be $true
            ($exported -contains "Set-CurrentAdbDevice") | Should Be $true
            ($exported -contains "Add-SavedAdbDevice") | Should Be $true
            ($exported -contains "Remove-SavedAdbDevice") | Should Be $true
            ($exported -contains "Update-SavedAdbDevice") | Should Be $true
            ($exported -contains "ConvertTo-AdbEndpoint") | Should Be $true
            ($exported -contains "Invoke-AdbDiagnostics") | Should Be $true
            ($exported -contains "Disconnect-AdbEndpoint") | Should Be $true
            ($exported -contains "Disconnect-AdbDevice") | Should Be $true
            ($exported -contains "Invoke-AdbShell") | Should Be $true
            ($exported -contains "Invoke-AdbPull") | Should Be $true
            ($exported -contains "Invoke-AdbPush") | Should Be $true
            ($exported -contains "Install-AdbPackage") | Should Be $true
            ($exported -contains "Add-AdbForward") | Should Be $true
            ($exported -contains "Pair-AdbDevice") | Should Be $true
            ($exported -contains "Get-AdbProperty") | Should Be $true
        }
    }

    Context "Contrato Unificado ADB (AndroidAdb Fase 1)" {
        It "Get-SingleReadyDevice falla si se llama sin serial y no hay dispositivos conectados" {
            Mock Get-AdbDevices { return @() } -ModuleName AndroidAdb
            { Get-SingleReadyDevice } | Should Throw "DISPOSITIVO_NO_SELECCIONADO"
        }

        It "Get-SingleReadyDevice falla si hay multiples dispositivos y no se especifica serial" {
            Mock Get-AdbDevices {
                return @(
                    [pscustomobject]@{ Serial = "DEV1"; Status = "device" },
                    [pscustomobject]@{ Serial = "DEV2"; Status = "device" }
                )
            } -ModuleName AndroidAdb
            { Get-SingleReadyDevice } | Should Throw "MULTIPLE_DEVICES"
        }

        It "Get-SingleReadyDevice retorna el dispositivo unico si solo hay uno en estado device" {
            Mock Get-AdbDevices {
                return @(
                    [pscustomobject]@{ Serial = "SOLO_UNO"; Status = "device" }
                )
            } -ModuleName AndroidAdb
            $dev = Get-SingleReadyDevice
            $dev.Serial | Should Be "SOLO_UNO"
        }

        It "Get-SingleReadyDevice resuelve el dispositivo indicado si se proporciona -Serial" {
            Mock Get-AdbDevices {
                return @(
                    [pscustomobject]@{ Serial = "DEV1"; Status = "device" },
                    [pscustomobject]@{ Serial = "DEV2"; Status = "device" }
                )
            } -ModuleName AndroidAdb
            $dev = Get-SingleReadyDevice -Serial "DEV2"
            $dev.Serial | Should Be "DEV2"
        }

        It "Disconnect-AdbEndpoint normaliza y desconecta via adb disconnect" {
            Mock Test-AdbAvailable { return $true } -ModuleName AndroidAdb
            Mock Invoke-AdbHost { return @("disconnected 192.168.1.50:5555") } -ModuleName AndroidAdb
            $res = Disconnect-AdbEndpoint -Endpoint "192.168.1.50:5555"
            $res | Should Match "disconnected"
        }

        It "Disconnect-AdbDevice falla limpiamente al intentar desconectar USB fisico" {
            { Disconnect-AdbDevice -Serial "USB123456" } | Should Throw "USB_DISCONNECT_UNSUPPORTED"
        }
    }
}
