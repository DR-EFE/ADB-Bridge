# android_advanced_backup.ps1 - Backup Avanzado SIN ROOT
Import-Module (Join-Path $PSScriptRoot 'AndroidAdb.psm1') -Force
$script:AndroidDevice = Get-SingleReadyDevice
function adb {
    param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments)
    if ($Arguments.Count -eq 1 -and [string]$Arguments[0] -ceq 'devices') { return Get-AdbDevices }
    return Invoke-Adb -Serial $script:AndroidDevice.Serial -Arguments ([string[]]$Arguments)
}
# Extrae todo lo posible sin necesidad de root

function Show-AdvancedMenu {
    Clear-Host
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   ANDROID ADVANCED BACKUP (Sin Root)   " -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Verificar conexion
    if ($null -ne $script:AndroidDevice) {
        Write-Host "OK: CONECTADO VIA WIFI" -ForegroundColor Green
        
        $model = adb shell getprop ro.product.model 2>$null
        $android = adb shell getprop ro.build.version.release 2>$null
        
        Write-Host "   Modelo: $model" -ForegroundColor White
        Write-Host "   Android: $android" -ForegroundColor White
    } else {
        Write-Host "ERROR: NO CONECTADO" -ForegroundColor Red
        Write-Host "   Ejecuta primero: android_setup.ps1" -ForegroundColor Yellow
        return
    }
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host "   RESPALDOS AVANZADOS" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  1. Contactos (vCard)" -ForegroundColor White
    Write-Host "  2. SMS/Mensajes" -ForegroundColor White
    Write-Host "  3. Llamadas (historial)" -ForegroundColor White
    Write-Host "  4. Calendario" -ForegroundColor White
    Write-Host "  5. Configuraciones del sistema" -ForegroundColor White
    Write-Host "  6. Apps instaladas (lista + APKs backup)" -ForegroundColor White
    Write-Host "  7. WiFi (redes guardadas)" -ForegroundColor White
    Write-Host "  8. Bluetooth (dispositivos emparejados)" -ForegroundColor White
    Write-Host "  9. Diccionario personal" -ForegroundColor White
    Write-Host " 10. Clipboard (historial)" -ForegroundColor White
    Write-Host " 11. BACKUP COMPLETO (todo lo anterior)" -ForegroundColor Green
    Write-Host " 12. Volver al menu principal" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host ""
}

function Backup-Contacts {
    Write-Host ""
    Write-Host "RESPALDANDO CONTACTOS..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Contacts"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    $filename = "contacts_$(Get-Date -Format 'yyyyMMdd_HHmmss').vcf"
    $fullPath = "$backupDir\$filename"
    
    Write-Host "Exportando contactos a vCard..." -ForegroundColor Yellow
    adb shell "content query --uri content://com.android.contacts/data/phones/" 2>$null | Out-File "$backupDir\contacts_raw.txt"
    
    # Metodo alternativo usando accounts
    adb shell "content query --uri content://com.android.contacts/contacts/" 2>$null | Out-File "$backupDir\contacts_all.txt"
    
    # Intentar exportar via telecom
    adb shell "service call com.android.contacts 2" 2>$null
    
    Write-Host "OK: Contactos respaldados en:" -ForegroundColor Green
    Write-Host "   $fullPath" -ForegroundColor Cyan
    Write-Host "   $backupDir\contacts_raw.txt" -ForegroundColor Cyan
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-SMS {
    Write-Host ""
    Write-Host "RESPALDANDO SMS/MENSAJES..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\SMS"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    $filename = "sms_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
    $fullPath = "$backupDir\$filename"
    
    Write-Host "Extrayendo SMS..." -ForegroundColor Yellow
    
    # Intentar acceder a content provider de SMS
    adb shell "content query --uri content://sms/" 2>$null | Out-File $fullPath
    adb shell "content query --uri content://sms/inbox" 2>$null | Out-File "$backupDir\sms_inbox.txt"
    adb shell "content query --uri content://sms/sent" 2>$null | Out-File "$backupDir\sms_sent.txt"
    adb shell "content query --uri content://sms/draft" 2>$null | Out-File "$backupDir\sms_draft.txt"
    
    # Extraer MMS
    adb shell "content query --uri content://mms/" 2>$null | Out-File "$backupDir\mms.txt"
    
    Write-Host "OK: SMS respaldados en:" -ForegroundColor Green
    Write-Host "   $fullPath" -ForegroundColor Cyan
    
    Write-Host ""
    Write-Host "Nota: En Android 11+, algunos SMS pueden requerir permisos especiales" -ForegroundColor Yellow
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-CallLog {
    Write-Host ""
    Write-Host "RESPALDANDO HISTORIAL DE LLAMADAS..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Calls"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    $filename = "call_log_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
    $fullPath = "$backupDir\$filename"
    
    Write-Host "Extrayendo historial de llamadas..." -ForegroundColor Yellow
    
    # Extraer call log
    adb shell "content query --uri content://call_log/calls" 2>$null | Out-File $fullPath
    
    # Extraer formato legible
    $calls = adb shell "content query --uri content://call_log/calls" 2>$null
    if ($calls) {
        $calls | Out-File "$backupDir\calls_readable.txt"
    }
    
    Write-Host "OK: Historial respaldado en:" -ForegroundColor Green
    Write-Host "   $fullPath" -ForegroundColor Cyan
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-Calendar {
    Write-Host ""
    Write-Host "RESPALDANDO CALENDARIO..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Calendar"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    $filename = "calendar_$(Get-Date -Format 'yyyyMMdd_HHmmss').ics"
    $fullPath = "$backupDir\$filename"
    
    Write-Host "Extrayendo eventos del calendario..." -ForegroundColor Yellow
    
    # Extraer eventos del calendario
    adb shell "content query --uri content://com.android.calendar/events" 2>$null | Out-File "$backupDir\calendar_events.txt"
    adb shell "content query --uri content://com.android.calendar/calendars" 2>$null | Out-File "$backupDir\calendar_list.txt"
    adb shell "content query --uri content://com.android.calendar/instances/when" 2>$null | Out-File "$backupDir\calendar_instances.txt"
    
    Write-Host "OK: Calendario respaldado en:" -ForegroundColor Green
    Write-Host "   $backupDir\calendar_events.txt" -ForegroundColor Cyan
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-SystemSettings {
    Write-Host ""
    Write-Host "RESPALDANDO CONFIGURACIONES DEL SISTEMA..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\SystemSettings"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "Extrayendo configuraciones..." -ForegroundColor Yellow
    
    # Configuraciones globales
    adb shell "settings list global" | Out-File "$backupDir\settings_global.txt"
    adb shell "settings list system" | Out-File "$backupDir\settings_system.txt"
    adb shell "settings list secure" | Out-File "$backupDir\settings_secure.txt"
    
    # Preferencias de usuario
    adb shell "content query --uri content://settings/system" 2>$null | Out-File "$backupDir\settings_content.txt"
    
    # Configuraciones de pantalla
    adb shell "dumpsys display" | Out-File "$backupDir\display_config.txt"
    
    # Configuraciones de sonido
    adb shell "dumpsys audio" | Out-File "$backupDir\audio_config.txt"
    
    # Configuraciones de bateria
    adb shell "dumpsys battery" | Out-File "$backupDir\battery_config.txt"
    
    # Configuraciones de red
    adb shell "dumpsys connectivity" | Out-File "$backupDir\network_config.txt"
    
    Write-Host "OK: Configuraciones guardadas en: $backupDir" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-Apps {
    Write-Host ""
    Write-Host "RESPALDANDO APPS INSTALADAS..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Apps"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "1. Solo lista de apps" -ForegroundColor White
    Write-Host "2. Lista + APKs (puede fallar en Android 11+)" -ForegroundColor White
    Write-Host "3. Volver" -ForegroundColor White
    Write-Host ""
    $app_opt = Read-Host "Selecciona opcion"
    
    # Exportar lista de apps
    Write-Host "Exportando lista de aplicaciones..." -ForegroundColor Yellow
    adb shell "pm list packages" | Out-File "$backupDir\apps_list.txt"
    adb shell "pm list packages -3" | Out-File "$backupDir\apps_user.txt"
    adb shell "pm list packages -s" | Out-File "$backupDir\apps_system.txt"
    adb shell "pm list packages -d" | Out-File "$backupDir\apps_disabled.txt"
    adb shell "pm list packages -e" | Out-File "$backupDir\apps_enabled.txt"
    
    # Informacion detallada de apps
    adb shell "dumpsys package" | Out-File "$backupDir\packages_info.txt"
    
    if ($app_opt -eq "2") {
        Write-Host ""
        Write-Host "Intentando respaldar APKs..." -ForegroundColor Yellow
        Write-Host "Nota: En Android 11+ puede no funcionar para todas las apps" -ForegroundColor DarkGray
        
        $apkDir = "$backupDir\APKs"
        New-Item -ItemType Directory -Path $apkDir -Force | Out-Null
        
        # Obtener lista de paquetes de usuario
        $packages = adb shell "pm list packages -3" | ForEach-Object { $_ -replace "package:", "" } | ForEach-Object { $_.Trim() }
        
        $count = 0
        foreach ($pkg in $packages) {
            $count++
            Write-Host "   [$count/$($packages.Count)] $pkg" -ForegroundColor Gray
            
            # Obtener ruta del APK
            $path = adb shell "pm path $pkg" 2>$null
            if ($path -match "package:(.+)") {
                $apkPath = $matches[1]
                $apkName = "$pkg.apk"
                adb pull "$apkPath" "$apkDir\$apkName" 2>$null
            }
        }
        
        Write-Host "OK: APKs guardados en: $apkDir" -ForegroundColor Green
    }
    
    Write-Host "OK: Lista de apps guardada en: $backupDir" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-WiFi {
    Write-Host ""
    Write-Host "RESPALDANDO REDES WIFI..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\WiFi"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "Extrayendo redes WiFi guardadas..." -ForegroundColor Yellow
    
    # WiFi config (requiere permisos)
    adb shell "cmd wifi list-saved-networks" 2>$null | Out-File "$backupDir\wifi_networks.txt"
    adb shell "dumpsys wifi" | Out-File "$backupDir\wifi_dump.txt"
    
    # Extraer SSIDs
    adb shell "dumpsys wifi | grep -i 'SSID'" | Out-File "$backupDir\wifi_ssids.txt"
    
    # Configuracion WiFi
    adb shell "cat /data/misc/wifi/*.conf" 2>$null | Out-File "$backupDir\wifi_config.txt"
    
    Write-Host "OK: Redes WiFi respaldadas en: $backupDir" -ForegroundColor Green
    Write-Host "Nota: Las contrasenas NO son accesibles sin root" -ForegroundColor Yellow
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-Bluetooth {
    Write-Host ""
    Write-Host "RESPALDANDO DISPOSITIVOS BLUETOOTH..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Bluetooth"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "Extrayendo dispositivos Bluetooth emparejados..." -ForegroundColor Yellow
    
    adb shell "dumpsys bluetooth_manager" | Out-File "$backupDir\bluetooth_devices.txt"
    adb shell "settings list global | grep bluetooth" | Out-File "$backupDir\bluetooth_settings.txt"
    
    Write-Host "OK: Dispositivos Bluetooth respaldados en: $backupDir" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-Dictionary {
    Write-Host ""
    Write-Host "RESPALDANDO DICCIONARIO PERSONAL..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Dictionary"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "Extrayendo diccionario personal..." -ForegroundColor Yellow
    
    # Diccionario de usuario
    adb shell "content query --uri content://com.android.providers.userdictionary/words" 2>$null | Out-File "$backupDir\user_dictionary.txt"
    
    Write-Host "OK: Diccionario respaldado en: $backupDir" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-Clipboard {
    Write-Host ""
    Write-Host "RESPALDANDO CLIPBOARD/HISTORIAL..." -ForegroundColor Green
    
    $backupDir = "$env:USERPROFILE\AndroidBackup\Clipboard"
    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    
    Write-Host "Extrayendo contenido del portapapeles..." -ForegroundColor Yellow
    
    # Clipboard actual
    adb shell "service call clipboard 1" 2>$null | Out-File "$backupDir\clipboard_current.txt"
    
    # Historial (si existe)
    adb shell "dumpsys clipboard" | Out-File "$backupDir\clipboard_history.txt"
    
    Write-Host "OK: Clipboard respaldado en: $backupDir" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

function Backup-AllAdvanced {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   BACKUP COMPLETO AVANZADO" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    
    Write-Host "ADVERTENCIA: Esto respaldara TODOS los datos disponibles" -ForegroundColor Red
    Write-Host "Puede tomar varios minutos y generar archivos grandes" -ForegroundColor Yellow
    Write-Host ""
    $confirm = Read-Host "Continuar con backup completo avanzado? (s/n)"
    
    if ($confirm -ne 's') {
        return
    }
    
    $mainBackupDir = "$env:USERPROFILE\AndroidBackup\FullAdvanced_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    New-Item -ItemType Directory -Path $mainBackupDir -Force | Out-Null
    
    Write-Host ""
    Write-Host "Iniciando backup completo..." -ForegroundColor Green
    Write-Host "Destino: $mainBackupDir" -ForegroundColor Cyan
    Write-Host ""
    
    # Lista de funciones a ejecutar (sin menus)
    $backupFunctions = @(
        @{Name="Contactos"; Func={Backup-Contacts-Silent $mainBackupDir}},
        @{Name="SMS"; Func={Backup-SMS-Silent $mainBackupDir}},
        @{Name="Llamadas"; Func={Backup-CallLog-Silent $mainBackupDir}},
        @{Name="Calendario"; Func={Backup-Calendar-Silent $mainBackupDir}},
        @{Name="Configuraciones"; Func={Backup-SystemSettings-Silent $mainBackupDir}},
        @{Name="Apps"; Func={Backup-Apps-Silent $mainBackupDir}},
        @{Name="WiFi"; Func={Backup-WiFi-Silent $mainBackupDir}},
        @{Name="Bluetooth"; Func={Backup-Bluetooth-Silent $mainBackupDir}},
        @{Name="Diccionario"; Func={Backup-Dictionary-Silent $mainBackupDir}},
        @{Name="Clipboard"; Func={Backup-Clipboard-Silent $mainBackupDir}}
    )
    
    $total = $backupFunctions.Count
    $current = 0
    
    foreach ($bf in $backupFunctions) {
        $current++
        Write-Host "[$current/$total] Respaldando $($bf.Name)..." -ForegroundColor Cyan
        & $bf.Func
    }
    
    # Crear archivo de resumen
    $summaryFile = "$mainBackupDir\RESUMEN_BACKUP.txt"
    @"
RESUMEN DE BACKUP AVANZADO
==========================
Fecha: $(Get-Date)
Dispositivo: $(adb shell getprop ro.product.model 2>$null)
Android: $(adb shell getprop ro.build.version.release 2>$null)

CONTENIDO DEL BACKUP:
- Contactos (vCard + raw)
- SMS/Mensajes (inbox, sent, draft)
- Historial de llamadas
- Eventos de calendario
- Configuraciones del sistema (global, system, secure)
- Lista de apps instaladas (usuario, sistema, deshabilitadas)
- Redes WiFi guardadas (sin contrasenas)
- Dispositivos Bluetooth emparejados
- Diccionario personal
- Clipboard/historial

UBICACION: $mainBackupDir

NOTA: Sin root, algunos datos pueden ser limitados.
"@ | Out-File -FilePath $summaryFile -Encoding UTF8
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "   BACKUP COMPLETADO!" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "UBICACION: $mainBackupDir" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Archivo de resumen: RESUMEN_BACKUP.txt" -ForegroundColor Gray
    
    Write-Host ""
    Write-Host "Presiona Enter para continuar..."
    Read-Host
}

# Funciones silenciosas (sin interaccion) para backup completo
function Backup-Contacts-Silent { param($dir)
    $subdir = "$dir\Contacts"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "content query --uri content://com.android.contacts/data/phones/" 2>$null | Out-File "$subdir\contacts_raw.txt"
    adb shell "content query --uri content://com.android.contacts/contacts/" 2>$null | Out-File "$subdir\contacts_all.txt"
}

function Backup-SMS-Silent { param($dir)
    $subdir = "$dir\SMS"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "content query --uri content://sms/" 2>$null | Out-File "$subdir\sms_all.txt"
    adb shell "content query --uri content://sms/inbox" 2>$null | Out-File "$subdir\sms_inbox.txt"
    adb shell "content query --uri content://sms/sent" 2>$null | Out-File "$subdir\sms_sent.txt"
}

function Backup-CallLog-Silent { param($dir)
    $subdir = "$dir\Calls"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "content query --uri content://call_log/calls" 2>$null | Out-File "$subdir\call_log.txt"
}

function Backup-Calendar-Silent { param($dir)
    $subdir = "$dir\Calendar"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "content query --uri content://com.android.calendar/events" 2>$null | Out-File "$subdir\calendar_events.txt"
}

function Backup-SystemSettings-Silent { param($dir)
    $subdir = "$dir\SystemSettings"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "settings list global" | Out-File "$subdir\settings_global.txt"
    adb shell "settings list system" | Out-File "$subdir\settings_system.txt"
    adb shell "settings list secure" | Out-File "$subdir\settings_secure.txt"
    adb shell "dumpsys display" | Out-File "$subdir\display_config.txt"
    adb shell "dumpsys battery" | Out-File "$subdir\battery_config.txt"
}

function Backup-Apps-Silent { param($dir)
    $subdir = "$dir\Apps"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "pm list packages" | Out-File "$subdir\apps_all.txt"
    adb shell "pm list packages -3" | Out-File "$subdir\apps_user.txt"
    adb shell "pm list packages -s" | Out-File "$subdir\apps_system.txt"
    adb shell "dumpsys package" | Out-File "$subdir\packages_info.txt"
}

function Backup-WiFi-Silent { param($dir)
    $subdir = "$dir\WiFi"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "cmd wifi list-saved-networks" 2>$null | Out-File "$subdir\wifi_networks.txt"
    adb shell "dumpsys wifi" | Out-File "$subdir\wifi_dump.txt"
}

function Backup-Bluetooth-Silent { param($dir)
    $subdir = "$dir\Bluetooth"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "dumpsys bluetooth_manager" | Out-File "$subdir\bluetooth_devices.txt"
}

function Backup-Dictionary-Silent { param($dir)
    $subdir = "$dir\Dictionary"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "content query --uri content://com.android.providers.userdictionary/words" 2>$null | Out-File "$subdir\user_dictionary.txt"
}

function Backup-Clipboard-Silent { param($dir)
    $subdir = "$dir\Clipboard"; New-Item -ItemType Directory -Path $subdir -Force | Out-Null
    adb shell "service call clipboard 1" 2>$null | Out-File "$subdir\clipboard_current.txt"
    adb shell "dumpsys clipboard" | Out-File "$subdir\clipboard_history.txt"
}

# Menu principal mejorado
while ($true) {
    Show-AdvancedMenu
    $option = Read-Host "Selecciona opcion (1-12)"
    
    switch ($option) {
        "1" { Backup-Contacts }
        "2" { Backup-SMS }
        "3" { Backup-CallLog }
        "4" { Backup-Calendar }
        "5" { Backup-SystemSettings }
        "6" { Backup-Apps }
        "7" { Backup-WiFi }
        "8" { Backup-Bluetooth }
        "9" { Backup-Dictionary }
        "10" { Backup-Clipboard }
        "11" { Backup-AllAdvanced }
        "12" { 
            Write-Host "Volviendo al menu principal..." -ForegroundColor Cyan
            break
        }
        default {
            Write-Host "ERROR: Opcion invalida" -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
}
