# Modulo Forense de Metadatos Fotograficos y Geolocalizacion (EXIF / GPS)

Herramienta forense y de analisis geoespacial para extraccion, normalizacion, agrupamiento territorial y visualizacion interactiva de metadatos de fotografias digitales procedentes de dispositivos Android, carpetas locales o archivos comprimidos.

---

## 🎯 Caracteristicas Principales

- **Multiples Modos de Entrada**:
  - **Imagen unica**: Analisis individual instantaneo de una fotografia.
  - **Carpeta local**: Procesamiento recursivo de lotes de imagenes en disco.
  - **Archivos comprimidos (`.zip`, `.rar`, `.tar`, `.7z`)**: Descompresion automatica y obligatoria en un **directorio dedicado independiente** (`archive_extracted/`) para mantener la integridad forense.
  - **Dispositivo Android (ADB)**: Escaneo inteligente de rutas multimedia (`DCIM`, `Pictures`, `Download`, `WhatsApp`, `Telegram`), descarga selectiva o reutilizacion de respaldos previos.
- **Estrategia Hibrida de Extraccion EXIF**:
  - Deteccion prioritaria de `metadata\exiftool.exe` y `exiftool` en `PATH`.
  - Descargador asistido de version portable oficial sin dependencias externas.
  - **Extractor nativo .NET puro** (`System.Drawing.Image`): Garantiza funcionamiento al 100% de manera autonoma y fuera de linea en cualquier instalacion de Windows, incluso sin ExifTool.
- **Analisis Forense y Agrupacion Geoespacial**:
  - Conversion automatica de coordenadas GPS a grados decimales.
  - Deteccion y calculo de clusters por proximidad geografica (redondeo a 3 decimales, radio aprox. de 110 metros).
  - Distribucion temporal cronologica (ano/mes).
  - Inventario de dispositivos (Marca/Modelo) y parametros de disparo (ISO, Apertura, Tiempo de Exposicion).
  - Deteccion de imagenes manipuladas o editadas (Photoshop, Lightroom, Snapseed).
  - Deteccion de imagenes "stripped" (sin metadatos EXIF).
- **Visualizacion Interactiva**:
  - `map.html`: Mapa interactivo con Leaflet, agrupamiento dinamico (`Leaflet.markercluster`), mapa de calor de densidad (`Leaflet.heat`), miniaturas generadas localmente y enlaces directos a Google Maps y OpenStreetMap.
  - `dashboard.html`: Tablero forense oscuro autocontenido con graficos en tiempo real (Chart.js), tabla de clusters, resumen estadistico y visor de catalogo completo.
- **Cadena de Custodia y Evidencia**:
  - Generacion de hashes criptograficos SHA-256 para todos los artefactos de salida.
  - Manifiesto sellado en formato JSON (`evidence_manifest.json`).
  - Reporte sintetico de auditoria (`README.txt`) y bitacora secuencial (`module.log`).
  - Empaquetado final automatizado en `.zip`.

---

## 📂 Estructura de Salida

Cada ejecucion genera un directorio con marca temporal dentro de `Downloads\AndroidBackup`:

```text
Downloads\AndroidBackup\Metadata_YYYYMMDD_HHMMSS\
├── source\                    # Copia aislada de las imagenes analizadas
├── archive_extracted\         # Directorio dedicado de descompresion (si se uso un .zip / .rar)
├── thumbs\                    # Miniaturas optimizadas para el mapa y dashboard
├── raw_metadata.csv           # Extraccion EXIF cruda (todas las etiquetas disponibles)
├── normalized_metadata.json   # Esquema normalizado forense (JSON estructurado)
├── normalized_metadata.csv    # Tabla plana para Excel o herramientas forenses
├── analysis_summary.json      # Metricas agregadas, clusters geoespaciales y estadisticas
├── map.html                   # Mapa interactivo Leaflet
├── dashboard.html             # Dashboard forense completo
├── evidence_manifest.json     # Manifiesto forense con hashes SHA-256 y metadata de sesion
├── README.txt                 # Resumen ejecutivo de la ejecucion
├── module.log                 # Registro cronologico de la operacion
└── Metadata_YYYYMMDD_HHMMSS.zip (opcional) # Paquete consolidado de evidencia
```

---

## 🚀 Formas de Ejecucion

### 1. Acceso Rapido (Recomendado)
Haga doble clic en el lanzador:
```cmd
METADATA.bat
```
Desplegara un menu interactivo en consola para elegir el origen de los datos.

### 2. Linea de Comandos (PowerShell)

#### Analizar una sola imagen:
```powershell
.\android_photo_metadata.ps1 -ImagePath "C:\Evidencias\foto_sospechosa.jpg"
```

#### Analizar una carpeta completa:
```powershell
.\android_photo_metadata.ps1 -FolderPath "C:\Fotos_Caso_01"
```

#### Analizar un archivo comprimido (.zip, .rar, .tar, .7z):
```powershell
.\android_photo_metadata.ps1 -ArchivePath "C:\Descargas\fotos_incautadas.rar"
```
*El script creara automaticamente el directorio aislado `archive_extracted/`, descomprimira el contenido con las utilidades del sistema (WinRAR, 7-Zip, tar o Expand-Archive) y procedera con el analisis.*

#### Escanear dispositivo Android conectado via ADB:
```powershell
# Modo interactivo con seleccion de carpetas
.\android_photo_metadata.ps1 -Device

# Modo reuso de respaldo previo (ahorra tiempo si ya se descargo previamente)
.\android_photo_metadata.ps1 -Device -Reuse

# Modo descarga fresca obligatoria
.\android_photo_metadata.ps1 -Device -Fresh
```

#### Descargar la version portable oficial de ExifTool:
```powershell
.\android_photo_metadata.ps1 -DownloadExifTool
```

---

## 🔄 Flujo de Trabajo en 7 Fases

```text
[1] Descubrimiento
       │
       ▼
[2] Adquisicion / Staging (Directorio dedicado para .rar/.zip)
       │
       ▼
[3] Extraccion EXIF (ExifTool / Fallback .NET)
       │
       ▼
[4] Normalizacion de Esquema (JSON / CSV)
       │
       ▼
[5] Analisis Geoespacial y Agregado (Clusters de ~110m)
       │
       ▼
[6] Generacion Visual (map.html + dashboard.html)
       │
       ▼
[7] Sellado de Evidencia (Hashes SHA-256 + evidence_manifest.json)
```

---

## 🔒 Privacidad y Seguridad de Datos

1. **Procesamiento 100% Local**: Ninguna imagen, coordenada o metadato sale de la estacion de trabajo ni se transmite a ningun servidor externo ni servicio de IA.
2. **Uso de Red**: La conexion a internet solo es utilizada por el navegador para renderizar los mosaicos cartograficos (OpenStreetMap/CartoDB) y bibliotecas CDN (Leaflet, Chart.js) al abrir los archivos HTML. Los datos JSON estan embebidos de manera estatica en el propio archivo.
3. **No alteracion de scripts base**: Este modulo opera de forma desacoplada de `android_transfer.ps1` y `android_list_downloadable.ps1`.

