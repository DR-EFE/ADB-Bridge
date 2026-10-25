# Laboratorio de PhantomDroid en Android Tools

## Propósito

Este documento describe cómo estudiar, aislar y adaptar capacidades de C:\PhantomDroid dentro de la rama Laboratorio de Android Tools.

El objetivo es validar integraciones defensivas y de administración ADB sobre dispositivos propios, emuladores o equipos expresamente autorizados. No se pretende convertir Android Tools en una herramienta de intrusión.

Las funciones que generan payloads, abren shells remotos, intentan saltar la pantalla de bloqueo o extraen datos privados deben permanecer deshabilitadas o representadas mediante simulaciones.

## Estado actual

Android Tools es una aplicación para Windows con PowerShell, Python, FastAPI y Flet. Ya posee selección explícita de dispositivo y serial, pairing Wireless Debugging mediante TLS, descubrimiento mDNS, flujo TCP/IP legado por USB, timeouts, reintentos, logging, transferencia, explorador, respaldos y escaneo de red.

La lógica central está en [AndroidAdb.psm1](../AndroidAdb.psm1) y el diseño de conexión en [ARCHITECTURE.md](../ARCHITECTURE.md).

PhantomDroid es un CLI Python que agrupa administración ADB, información del dispositivo, escaneo de red, análisis estático de APK, comprobaciones de vulnerabilidades, explotación de componentes y generación de payloads. La integración debe hacerse por capacidades aisladas y no copiando el CLI completo.

## Matriz de integración

| Capacidad | Propuesta | Riesgo | Estado |
|---|---|---:|---|
| Dispositivos y paquetes | Adaptar a AdbService | Bajo | Integrable |
| Capturas y logcat | Enlazar con servicios/jobs existentes | Medio | Integrable con redacción |
| Pull/push | Mantener el núcleo actual | Medio | No duplicar wrapper ADB |
| Escaneo de red | Fusionar indicadores con network_scan_service | Medio | Solo redes autorizadas |
| Análisis estático de APK | Servicio aislado de lectura | Medio | Integrable |
| Reporte HTML/JSON | Adaptar al modelo actual | Bajo | Integrable |
| Comprobaciones de vulnerabilidades | Portar solo lecturas | Medio/alto | Con confirmación |
| ADB WiFi legado en 5555 | Usar flujo existente por USB | Alto | No copiar directamente |
| Shell ADB arbitrario | No exponer en GUI normal | Alto | Mock o consola de laboratorio |
| Componentes/deep links | Simular primero | Alto | Laboratorio separado |
| Bases privadas | No integrar al producto principal | Crítico | Evidencia simulada |
| Bypass de bloqueo | No integrar | Crítico | Excluir |
| Reverse shell/payloads | No integrar al producto | Crítico | Mock y detección |

## ADB WiFi

No debe copiarse directamente enable_adb_wifi de PhantomDroid. Esa función activa adbd sobre TCP/5555 y reinicia el servicio sin aplicar las políticas de selección, autorización y registro de Android Tools.

Android Tools debe:

1. Preferir Wireless Debugging moderno mediante pairing TLS.
2. Usar TCP/IP legado únicamente después de una conexión USB autorizada.
3. Asociar el endpoint a un dispositivo seleccionado y validado.
4. No abrir ADB hacia Internet ni guardar endpoints no verificados.
5. Registrar la operación, serial, modo y resultado.

La implementación existente para este flujo está en Invoke-AdbUsbWifiConversion dentro de [AndroidAdb.psm1](../AndroidAdb.psm1).

## Laboratorio seguro

### Requisitos

- Dispositivo Android de pruebas o emulador.
- Cuenta y datos ficticios.
- Depuración USB autorizada únicamente para el dispositivo de laboratorio.
- Red aislada, sin exposición a Internet ni a redes de terceros.
- Snapshots del emulador o una imagen restaurable.
- Directorio de resultados separado y protegido.
- Registro de fecha, dispositivo, prueba, resultado y limpieza.

La rama Git no aísla por sí sola los procesos. El aislamiento real debe hacerse con un emulador, una máquina virtual o una red de laboratorio.

### Windows

Windows es el entorno principal de Android Tools y es suficiente para ADB, pairing, capturas, logcat, transferencia, backups, GUI Flet, backend FastAPI y pruebas.

Preparación recomendada:

1. Instalar Android Platform-Tools y verificar que adb esté en PATH.
2. Instalar requirements.txt en un entorno virtual.
3. Usar un emulador o teléfono de pruebas con cuenta ficticia.
4. Ejecutar primero diagnósticos y pruebas unitarias.
5. Mantener desactivadas las funciones ofensivas mediante configuración de laboratorio.

Diferencias relevantes:

- Algunos comandos Unix de PhantomDroid no existen o tienen otra sintaxis.
- ping -c no equivale al ping de Windows.
- Bash, nc, msfvenom, apktool y utilidades similares requieren instalación independiente.
- Rutas, permisos y procesos hijos deben adaptarse a PowerShell.
- Las órdenes ADB deben pasarse como argumentos separados, no como strings completos.

### Linux

Linux suele ser más cómodo para análisis Android porque dispone de forma natural de Bash, OpenJDK, apktool, jadx y utilidades Unix. Es recomendable como estación separada para analizar APKs de prueba, ejecutar emuladores, validar scripts y preparar reportes estáticos antes de trasladar el servicio a Windows.

Linux no convierte una operación peligrosa en segura. Las mismas reglas de autorización, aislamiento y datos ficticios siguen siendo obligatorias.

## APKs en Windows y Linux

El análisis estático de APK puede hacerse en ambos sistemas.

En Windows el reto principal suele ser configurar PATH, Java, apktool y utilidades auxiliares. Es la opción adecuada si el resultado debe integrarse directamente en la GUI.

En Linux suele ser más sencillo automatizar el flujo de análisis y compatibilidad de herramientas. Es útil para prototipar y revisar APKs de prueba antes de portar el servicio.

Estrategia recomendada:

1. Crear un analizador de APK de solo lectura.
2. Aceptar únicamente una ruta seleccionada por el usuario.
3. Calcular el hash del archivo antes del análisis.
4. Extraer manifest, permisos, componentes exportados, URLs y cadenas potencialmente sensibles sin ejecutar el APK.
5. Guardar un reporte JSON con severidad y evidencia limitada.
6. Mostrar resultados en la GUI sin incluir secretos completos.
7. Probar el mismo contrato en Linux y Windows.

No se debe instalar automáticamente un APK analizado ni generar un APK con payloads desde el producto principal.

## Adaptación segura

La arquitectura recomendada es:

    GUI / API
       |
       +-- Job de laboratorio con confirmación explícita
               |
               +-- Adaptador seguro de AdbService
               |       - serial obligatorio
               |       - timeout y cancelación
               |       - allowlist de comandos
               |       - logging y redacción
               |
               +-- Analizador estático o comprobación de lectura
               |
               +-- Resultado normalizado y reporte

Reglas del adaptador:

- Nunca seleccionar silenciosamente un dispositivo cuando hay varios.
- Nunca aceptar un serial o endpoint sin validación.
- No permitir comandos arbitrarios desde campos de texto de la GUI.
- Aplicar timeout, cancelación y límite de salida.
- Redactar tokens, contraseñas, SMS, contactos y contenido de logcat.
- Registrar quién inició la prueba y sobre qué dispositivo.
- Requerir confirmación para acciones que cambien el estado del dispositivo.
- Mantener una lista explícita de comandos permitidos y bloqueados.

## Funciones simuladas

Para probar la interfaz sin ejecutar acciones dañinas se pueden usar fixtures:

- reverse_shell_requested: devuelve resultado bloqueado y la razón.
- payload_generation_requested: genera reporte de simulación, no un APK.
- lock_bypass_requested: registra que la operación fue rechazada.
- private_database_requested: devuelve REQUIERE_AUTORIZACION_Y_ROOT.
- exported_component_test: trabaja contra un emulador de prueba con una APK diseñada para la prueba.
- adb_wifi_legacy_requested: valida el flujo, pero no ejecuta activación fuera de una sesión USB confirmada.

Los fixtures permiten comprobar GUI, jobs, logs, errores, cancelación y reportes sin introducir payloads funcionales en el repositorio.

## Plan por fases

### Fase 1: controles

- Mantener esta rama separada.
- Añadir configuración LAB_MODE=true.
- Crear allowlist de dispositivos de laboratorio.
- Crear directorio de resultados aislado.
- Añadir pruebas para bloqueo de acciones críticas.

### Fase 2: integración defensiva

- Integrar análisis estático de APK.
- Integrar reportes JSON/HTML.
- Añadir comprobaciones de lectura del dispositivo.
- Fusionar indicadores de red con el escáner actual.

### Fase 3: compatibilidad

- Ejecutar pruebas en Windows.
- Repetir análisis estático y fixtures en Linux.
- Comparar contratos y reportes, no comandos específicos del sistema.
- Documentar diferencias de salida y dependencias.

### Fase 4: revisión

Antes de pasar algo a la rama principal debe existir prueba automatizada, revisión de seguridad, documentación de permisos y datos tratados, ausencia de persistencia o acceso remoto, manejo de errores y cancelación, compatibilidad Windows y limpieza de artefactos sensibles.

## Límites

No se deben trasladar a producción reverse shells, APKs maliciosos o modificados para control remoto, bypass de bloqueo, extracción de bases privadas, explotación automática de componentes, shell interactivo sin allowlist, activación automática de ADB TCP sin autorización y escaneo de redes que no pertenezcan al laboratorio.

Estas capacidades pueden documentarse como casos de rechazo, simulaciones y criterios de detección. No deben convertirse en botones operativos dentro de la aplicación principal.

## Referencias revisadas

- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [AndroidAdb.psm1](../AndroidAdb.psm1)
- [android_advanced_backup.ps1](../android_advanced_backup.ps1)
- [android_transfer.ps1](../android_transfer.ps1)
- C:\PhantomDroid\modules\adb_manager.py
- C:\PhantomDroid\modules\apk_analyzer.py
- C:\PhantomDroid\modules\network_scanner.py
- C:\PhantomDroid\modules\vulnerability_scanner.py
- C:\PhantomDroid\modules\exploit_engine.py
- C:\PhantomDroid\modules\payload_generator.py

