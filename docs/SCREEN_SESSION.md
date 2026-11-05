# Pantalla en vivo con scrcpy

La pantalla se abre en una ventana propia de scrcpy. La interfaz de Android Tools inicia una sesión para el serial ADB seleccionado y reutiliza el ejecutable ADB detectado por la aplicación para evitar mezclar versiones del servidor ADB.

## Ejecutable incluido

La distribución fija incluida en este repositorio es **scrcpy 5.0.1 para Windows x64**. El ejecutable esperado es:

```text
tools/scrcpy/scrcpy.exe
```

El paquete completo está en `tools/scrcpy/`, porque `scrcpy.exe` necesita sus DLL, recursos, `scrcpy-server` y `adb.exe` de respaldo. Si se reemplaza el paquete, conservar todos los archivos del nuevo archivo oficial y actualizar este documento con su versión y SHA-256.

Archivo oficial: `scrcpy-win64-v5.0.1.zip`  
SHA-256 verificado: `b12a2c4ee8be317422451fc7dcf8ee20a71b5ea7ef9ad73ddd825a25316227a5`

El paquete publicado incluye FFmpeg 9.0.2, SDL 3.4.18, libusb 1.0.30 y Android Platform Tools (ADB 37.0.1). La aplicación configura `ADB` al ejecutable encontrado por su servicio ADB antes de iniciar scrcpy, para que ambos usen la misma instalación/servidor.

Si la copia local no está disponible, el servicio busca `scrcpy`/`scrcpy.exe` en `PATH`. Si ambas búsquedas fallan, la interfaz muestra las dos opciones.

## Perfiles

| Perfil | Tamaño máximo | FPS máximo | Bitrate de video |
| --- | ---: | ---: | ---: |
| Baja | 800 px | 30 | 4 Mbps |
| Media (predeterminado) | 1280 px | 60 | 8 Mbps |
| Alta | 1920 px | 60 | 12 Mbps |

El servicio también define el perfil `native` (`--max-size 0`) para uso futuro; no se muestra en la UI. Mantener despierto está activado por defecto y apagar la pantalla del teléfono está desactivado.

La integración pasa `--no-audio`; en esta entrega solo se abre video y control.

## Licencias y avisos

Se conserva el `LICENSE.txt` distribuido por scrcpy y se incluyen textos LGPL 2.1 en `tools/scrcpy/licenses/` para FFmpeg y libusb. SDL 3 se distribuye bajo zlib; Android Platform Tools se distribuye bajo Apache 2.0. Las versiones, licencias y fuentes oficiales son:

- scrcpy: Apache 2.0, `tools/scrcpy/LICENSE.txt`, [código y release 5.0.1](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1).
- FFmpeg 9.0.2: LGPL 2.1 o posterior salvo partes opcionales GPL, [consideraciones oficiales de licencia](https://ffmpeg.org/legal.html) y [fuente 9.0.2](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz). Texto LGPL incluido localmente.
- SDL 3.4.18: zlib, [licencia oficial](https://www.libsdl.org/license.php).
- libusb 1.0.30: LGPL 2.1 o posterior, texto `tools/scrcpy/licenses/libusb-COPYING.txt` y [fuente oficial](https://github.com/libusb/libusb/tree/v1.0.30).
- Android Platform Tools 37.0.1: Apache 2.0, [descargas y términos](https://developer.android.com/tools/releases/platform-tools).

Al redistribuir este bundle, conservar estos avisos y licencias junto con los binarios. Las obligaciones de redistribución de fuentes de las bibliotecas LGPL deben evaluarse para el canal concreto de distribución.
