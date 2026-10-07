Este proyecto es una colección de scripts para administrar un teléfono Android desde Windows mediante ADB. Permite conectar el celular por USB o Wi‑Fi, explorar archivos, transferir datos y generar respaldos sin necesidad de root.

No es una aplicación Android instalada en el teléfono; es una herramienta de automatización para Windows basada en PowerShell y archivos `.bat`.

## Componentes principales

| Archivo | Para qué sirve |
|---|---|
| [android_setup_v2.ps1](C:/Users/pipe6/Downloads/Android/android_setup_v2.ps1) | Configura la conexión ADB por Wi‑Fi. Incluye métodos mediante código QR, código de emparejamiento y USB. Guarda la IP del teléfono para futuras conexiones. |
| [setup.ps1](C:/Users/pipe6/Downloads/Android/setup.ps1) | Punto de entrada de configuración. Delega en `android_setup_v2.ps1` para pairing moderno o TCP/IP USB legado. |
| [android_reconnect.ps1](C:/Users/pipe6/Downloads/Android/android_reconnect.ps1) | Lee la configuración guardada e intenta reconectar automáticamente el teléfono por Wi‑Fi. |
| [android_transfer.ps1](C:/Users/pipe6/Downloads/Android/android_transfer.ps1) | Menú principal para transferir fotos, videos y documentos, subir archivos al celular, hacer respaldos, buscar archivos y revisar el espacio disponible. |
| [android_file_explorer.ps1](C:/Users/pipe6/Downloads/Android/android_file_explorer.ps1) | Explorador interactivo de archivos del teléfono. Permite navegar carpetas, descargar/subir archivos, buscar elementos y consultar información. |
| [android_list_downloadable.ps1](C:/Users/pipe6/Downloads/Android/android_list_downloadable.ps1) | Descarga carpetas completas desde Android, incluyendo capturas de pantalla, fotos, videos, documentos, música y otras rutas seleccionadas. |
| [android_advanced_backup.ps1](C:/Users/pipe6/Downloads/Android/android_advanced_backup.ps1) | Realiza respaldos avanzados sin root de contactos, SMS, llamadas, calendario, aplicaciones, configuraciones, Wi‑Fi, Bluetooth, diccionario y portapapeles. |
| [AutorizacionWeb.ps1](C:/Users/pipe6/Downloads/Android/AutorizacionWeb.ps1) | Reinicia ADB y ayuda a resolver el estado `unauthorized`, solicitando autorización en el teléfono. Usa una IP fija configurada dentro del script. |

## Archivos de acceso rápido

| Archivo | Función |
|---|---|
| [SETUP.bat](C:/Users/pipe6/Downloads/Android/SETUP.bat) | Ejecuta la configuración básica por USB/Wi‑Fi. |
| [RECONNECT.bat](C:/Users/pipe6/Downloads/Android/RECONNECT.bat) | Ejecuta la reconexión rápida. |
| [TRANSFER.bat](C:/Users/pipe6/Downloads/Android/TRANSFER.bat) | Abre el menú de transferencia de archivos. |
| [Conectar_Android.cmd](C:/Users/pipe6/Downloads/Android/Conectar_Android.cmd) | Ejecuta el punto de entrada `setup.ps1`. |
| [ejecuta.txt](C:/Users/pipe6/Downloads/Android/ejecuta.txt) | Contiene comandos manuales para ejecutar los scripts. |

## Flujo general

```text
Teléfono Android
      │
      │ USB o Wi‑Fi
      ▼
ADB: adb devices / adb connect
      │
      ├── Configuración y reconexión
      ├── Transferencia de archivos
      ├── Explorador de archivos
      └── Respaldos básicos y avanzados
```

## Dependencias

- Windows.
- PowerShell.
- Android Debug Bridge (`adb`) instalado y disponible en el `PATH`.
- Depuración USB activada en el teléfono.
- Para conexión inalámbrica, el teléfono y la computadora deben estar en la misma red.
- Autorización ADB aceptada en el teléfono.

## Observaciones importantes

- El respaldo avanzado no garantiza acceso completo a datos privados de todas las aplicaciones; muchas funciones dependen de la versión de Android y de los permisos disponibles.
- El script guarda información sensible, como contactos, SMS, llamadas, redes Wi‑Fi y posibles datos del portapapeles.
- [AutorizacionWeb.ps1](C:/Users/pipe6/Downloads/Android/AutorizacionWeb.ps1) contiene una IP fija (`192.168.1.70`), por lo que puede requerir modificación.
- Los respaldos normalmente se guardan dentro de `Downloads\AndroidBackup`.
- En esta carpeta no encontré README ni documentación formal; la explicación anterior se basa en los nombres, menús y comandos implementados en los scripts.
