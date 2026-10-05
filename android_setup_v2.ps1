# android_setup_v2.ps1 - Version Corregida (USB)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   ANDROID WiFi SETUP (Android 11+)     " -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verificar ADB
$adbPath = Get-Command adb -ErrorAction SilentlyContinue
if (-not $adbPath) {
    Write-Host "ERROR: ADB no encontrado en PATH" -ForegroundColor Red
    exit 1
}

Write-Host "ADB Version: $(adb version)" -ForegroundColor Gray
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   ELIGE UN METODO                      " -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "   [1] CODIGO QR (Android 11+)" -ForegroundColor Green
Write-Host "   [2] CODIGO DE EMPAREJAMIENTO (Android 11+)" -ForegroundColor Cyan
Write-Host "   [3] METODO USB TRADICIONAL" -ForegroundColor Yellow
Write-Host ""
$metodo = Read-Host "Selecciona opcion (1, 2 o 3)"

switch ($metodo) {
    "1" {
        # Metodo QR
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "   EMPAREJAMIENTO POR QR                " -ForegroundColor Yellow
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host ""
        
        Write-Host "PASOS EN EL TELEFONO:" -ForegroundColor White
        Write-Host "1. Ajustes > Opciones de desarrollador" -ForegroundColor Gray
        Write-Host "2. Activa 'Depuracion inalambrica'" -ForegroundColor Yellow
        Write-Host "3. Toca 'Vincular dispositivo con codigo QR'" -ForegroundColor Green
        Write-Host ""
        
        Write-Host "Presiona Enter para GENERAR EL CODIGO QR..."
        Read-Host
        
        Write-Host ""
        Write-Host "GENERANDO CODIGO QR..." -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host ""
        
        # Generar QR
        $qrOutput = & adb pair --qr 2>&1
        Write-Host $qrOutput -ForegroundColor White
        
        Write-Host ""
        Write-Host "ESCANEA EL QR CON TU TELEFONO" -ForegroundColor Yellow
        Write-Host ""
        
        # Esperar conexion
        Write-Host "Esperando conexion..." -ForegroundColor Green
        $ip = $null
        for ($i = 1; $i -le 20; $i++) {
            Start-Sleep -Seconds 2
            $devices = adb devices
            Write-Host "  Intentando $i/20..." -ForegroundColor Gray
            
            if ($devices -match "(\d+\.\d+\.\d+\.\d+):\d+\s+device") {
                $fullMatch = $matches[0]
                $ip = ($fullMatch -split ':')[0]
                Write-Host ""
                Write-Host "OK: Dispositivo conectado! IP: $ip" -ForegroundColor Green
                break
            }
        }
        
        if (-not $ip) {
            Write-Host ""
            Write-Host "No se detecto automaticamente." -ForegroundColor Yellow
            $ip = Read-Host "Ingresa la IP manualmente"
            adb connect "${ip}:5555"
        }
        
        $global:ip_guardada = $ip
    }
    
    "2" {
        # Metodo codigo manual
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "   EMPAREJAMIENTO POR CODIGO            " -ForegroundColor Yellow
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host ""
        
        Write-Host "PASOS EN EL TELEFONO:" -ForegroundColor White
        Write-Host "1. Ajustes > Opciones de desarrollador" -ForegroundColor Gray
        Write-Host "2. Activa 'Depuracion inalambrica'" -ForegroundColor Yellow
        Write-Host "3. Toca 'Vincular dispositivo con codigo de emparejamiento'" -ForegroundColor Green
        Write-Host ""
        
        $ip = Read-Host "IP del telefono"
        $pairing_port = Read-Host "Puerto de emparejamiento"
        $pairing_code = Read-Host "Codigo de 6 digitos"
        
        Write-Host ""
        Write-Host "Emparejando..." -ForegroundColor Green
        adb pair "${ip}:${pairing_port}" $pairing_code
        Start-Sleep -Seconds 2
        
        Write-Host "Conectando..." -ForegroundColor Green
        adb connect "${ip}:5555"
        Start-Sleep -Seconds 2
        
        $global:ip_guardada = $ip
    }
    
    "3" {
        # METODO USB TRADICIONAL - CORREGIDO
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "   METODO USB TRADICIONAL               " -ForegroundColor Yellow
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host ""
        
        # Matar servidor ADB para empezar limpio
        Write-Host "Reiniciando servidor ADB..." -ForegroundColor Gray
        adb kill-server
        Start-Sleep -Seconds 2
        adb start-server
        Start-Sleep -Seconds 2
        
        Write-Host ""
        Write-Host "INSTRUCCIONES:" -ForegroundColor Yellow
        Write-Host "1. Conecta el telefono por USB" -ForegroundColor White
        Write-Host "2. Activa 'Depuracion USB' en Opciones de desarrollador" -ForegroundColor White
        Write-Host "3. Acepta el mensaje de depuracion en el telefono" -ForegroundColor White
        Write-Host ""
        Read-Host "Presiona Enter cuando estes listo"
        
        # Listar dispositivos correctamente
        Write-Host ""
        Write-Host "Buscando dispositivos USB..." -ForegroundColor Green
        
        $deviceOutput = adb devices -l
        Write-Host $deviceOutput -ForegroundColor Gray
        
        # Extraer el serial correctamente
        $usbDevice = $null
        $lines = $deviceOutput -split "`r`n"
        
        foreach ($line in $lines) {
            # Buscar linea que termine con "device" y no sea "unauthorized"
            if ($line -match "^([a-zA-Z0-9_\-\.]+)\s+device\s" -or $line -match "^([a-zA-Z0-9_\-\.]+)\s+device$") {
                $usbDevice = $matches[1]
                Write-Host "Dispositivo encontrado: $usbDevice" -ForegroundColor Green
                break
            }
            # Algunos dispositivos tienen serial largo
            if ($line -match "^([A-Z0-9]+)\s+device") {
                $usbDevice = $matches[1]
                Write-Host "Dispositivo encontrado: $usbDevice" -ForegroundColor Green
                break
            }
        }
        
        if (-not $usbDevice) {
            Write-Host ""
            Write-Host "ERROR: No se encontro dispositivo USB" -ForegroundColor Red
            Write-Host ""
            Write-Host "Verifica:" -ForegroundColor Yellow
            Write-Host "- El cable USB esta conectado" -ForegroundColor White
            Write-Host "- La depuracion USB esta activada" -ForegroundColor White
            Write-Host "- Aceptaste el mensaje en el telefono" -ForegroundColor White
            Write-Host ""
            Write-Host "Ejecuta 'adb devices' manualmente para verificar" -ForegroundColor Gray
            exit 1
        }
        
        # Verificar que no este unauthorized
        $authCheck = adb devices | Select-String "$usbDevice.*unauthorized"
        if ($authCheck) {
            Write-Host ""
            Write-Host "ERROR: Dispositivo no autorizado!" -ForegroundColor Red
            Write-Host "Revisa el telefono y acepta el mensaje de depuracion USB" -ForegroundColor Yellow
            Write-Host "Marca 'Siempre permitir desde este equipo'" -ForegroundColor Yellow
            exit 1
        }
        
        # Activar modo TCPIP
        Write-Host ""
        Write-Host "Activando modo TCP/IP en puerto 5555..." -ForegroundColor Green
        $tcpipResult = adb -s $usbDevice tcpip 5555
        Write-Host $tcpipResult -ForegroundColor Gray
        Start-Sleep -Seconds 5
        
        # OBTENER IP - Metodos mejorados
        $ip = $null
        
        Write-Host ""
        Write-Host "Obteniendo IP del dispositivo..." -ForegroundColor Green
        
        # Metodo 1: ip route (el mas confiable)
        Write-Host "  Probando metodo 1 (ip route)..." -ForegroundColor Gray
        $output = adb -s $usbDevice shell "ip route 2>/dev/null | grep -oP 'src \K[\d.]+'"
        if (-not $output) {
            $output = adb -s $usbDevice shell "ip route" 2>$null
        }
        
        if ($output -match "src\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})") {
            $ip = $matches[1]
            Write-Host "  IP encontrada: $ip" -ForegroundColor Green
        } elseif ($output -match "(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})") {
            $ip = $matches[1]
            Write-Host "  IP encontrada: $ip" -ForegroundColor Green
        }
        
        # Metodo 2: ifconfig wlan0
        if (-not $ip) {
            Write-Host "  Probando metodo 2 (ifconfig)..." -ForegroundColor Gray
            $output = adb -s $usbDevice shell "ifconfig wlan0" 2>$null
            Write-Host "    Salida: $output" -ForegroundColor Gray
            
            if ($output -match "inet addr:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})") {
                $ip = $matches[1]
                Write-Host "  IP encontrada: $ip" -ForegroundColor Green
            } elseif ($output -match "inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})") {
                $ip = $matches[1]
                Write-Host "  IP encontrada: $ip" -ForegroundColor Green
            }
        }
        
        # Metodo 3: ip addr show wlan0
        if (-not $ip) {
            Write-Host "  Probando metodo 3 (ip addr)..." -ForegroundColor Gray
            $output = adb -s $usbDevice shell "ip addr show wlan0" 2>$null
            Write-Host "    Salida: $output" -ForegroundColor Gray
            
            if ($output -match "inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})") {
                $ip = $matches[1]
                Write-Host "  IP encontrada: $ip" -ForegroundColor Green
            }
        }
        
        # Metodo 4: pedir manualmente
        if (-not $ip) {
            Write-Host ""
            Write-Host "  No se pudo obtener la IP automaticamente." -ForegroundColor Yellow
            Write-Host ""
            Write-Host "Para ver la IP en el telefono:" -ForegroundColor White
            Write-Host "  - Ajustes > Acerca del telefono > Estado > Direccion IP" -ForegroundColor Gray
            Write-Host "  - O: Ajustes > WiFi > Tocar red actual" -ForegroundColor Gray
            Write-Host ""
            $ip = Read-Host "Ingresa la IP manualmente"
        }
        
        # Conectar via WiFi
        Write-Host ""
        Write-Host "Conectando a $ip : 5555..." -ForegroundColor Green
        $connectResult = adb connect "${ip}:5555"
        Write-Host $connectResult -ForegroundColor Gray
        Start-Sleep -Seconds 3
        
        # Verificar conexion
        Write-Host ""
        Write-Host "Verificando conexion..." -ForegroundColor Green
        $devicesCheck = adb devices
        
        if ($devicesCheck -match "${ip}:5555.*device") {
            Write-Host ""
            Write-Host "========================================" -ForegroundColor Green
            Write-Host "   CONEXION EXITOSA!" -ForegroundColor Yellow
            Write-Host "========================================" -ForegroundColor Green
            
            $global:ip_guardada = $ip
            
            # Mostrar informacion del dispositivo
            $modelo = adb -s "${ip}:5555" shell getprop ro.product.model 2>$null
            if ($modelo) {
                Write-Host "Modelo: $modelo" -ForegroundColor Gray
            }
        } else {
            Write-Host ""
            Write-Host "ERROR: No se pudo conectar" -ForegroundColor Red
            Write-Host ""
            Write-Host "Posibles problemas:" -ForegroundColor Yellow
            Write-Host "1. El telefono y PC no estan en la misma red WiFi" -ForegroundColor White
            Write-Host "2. El WiFi del telefono esta apagado" -ForegroundColor White
            Write-Host "3. La IP es incorrecta" -ForegroundColor White
            Write-Host ""
            Write-Host "Dispositivos detectados:" -ForegroundColor Cyan
            Write-Host $devicesCheck
            exit 1
        }
    }
}

# Guardar configuracion
if ($global:ip_guardada) {
    Write-Host ""
    Write-Host "Guardando configuracion..." -ForegroundColor Green
    $configDir = "$env:USERPROFILE\AndroidTools"
    if (-not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }
    
    $configContent = @"
# Configuracion Android WiFi
ANDROID_IP="$global:ip_guardada"
ANDROID_PORT="5555"
SETUP_DATE="$(Get-Date)"

# Reconectar:
adb connect $global:ip_guardada:5555
"@
    
    $configContent | Out-File -FilePath "$configDir\android_config.txt" -Encoding ASCII
    Write-Host "OK: Configuracion guardada en $configDir\android_config.txt" -ForegroundColor Green
}

Write-Host ""
Write-Host "=================================================" -ForegroundColor Green
Write-Host "   CONFIGURACION COMPLETADA!" -ForegroundColor Yellow
Write-Host "=================================================" -ForegroundColor Green
Write-Host ""
Write-Host "IP del celular: $global:ip_guardada" -ForegroundColor Cyan
Write-Host ""
Write-Host "Ya puedes desconectar el USB" -ForegroundColor Yellow
Write-Host ""

pause