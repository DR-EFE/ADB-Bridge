[CmdletBinding()]
param(
    [string]$SdkRoot,
    [string]$Output
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($Output)) { $Output = Join-Path $scriptRoot 'build\AndroidToolsReceiver.apk' }
$project = $scriptRoot
$src = Join-Path $project 'app\src\main'
$build = Join-Path $project 'build'
$classes = Join-Path $build 'classes'
$dex = Join-Path $build 'dex'
$generated = Join-Path $build 'generated'

# Detectar SDK Root automáticamente si no se especificó o es inválido
if ([string]::IsNullOrWhiteSpace($SdkRoot) -or (-not (Test-Path -LiteralPath $SdkRoot))) {
    $foundSdk = $null
    $candidateSdkPaths = @(
        'C:\Program Files (x86)\Android\android-sdk',
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
        'C:\Program Files\Android\android-sdk',
        $env:ANDROID_HOME,
        $env:ANDROID_SDK_ROOT
    )
    foreach ($cand in $candidateSdkPaths) {
        if (-not [string]::IsNullOrWhiteSpace($cand) -and (Test-Path -LiteralPath $cand)) {
            $foundSdk = $cand
            break
        }
    }

    if ($foundSdk) {
        $SdkRoot = $foundSdk
    } else {
        throw "No se encontró el Android SDK en las rutas estándar. Especifica -SdkRoot."
    }
}

Write-Host "Usando SdkRoot: $SdkRoot"

# Detectar android.jar más reciente disponible
$platformsDir = Join-Path $SdkRoot 'platforms'
Write-Host "Buscando platforms en: $platformsDir"
$platformJars = @(Get-ChildItem -Path $platformsDir -Filter 'android.jar' -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)
if ($platformJars.Count -eq 0) { throw "No se encontró android.jar en $platformsDir" }
$androidJar = $platformJars[0].FullName

# Detectar versión de build-tools compatible
$buildToolsDir = Join-Path $SdkRoot 'build-tools'
$buildToolsVersions = @(Get-ChildItem -Path $buildToolsDir -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
if ($buildToolsVersions.Count -eq 0) { throw "No se encontraron build-tools en $buildToolsDir" }

# Si hay varias versiones, buscar preferentemente una compatible con Java 8 (<= 32.x) o usar la disponible
$buildTools = $buildToolsVersions[0].FullName
$compatibleTools = $buildToolsVersions | Where-Object { $_.Name -like '32.*' -or $_.Name -like '30.*' }
if ($compatibleTools) {
    $buildTools = ($compatibleTools | Select-Object -First 1).FullName
}

$aapt2 = Join-Path $buildTools 'aapt2.exe'
$d8 = Join-Path $buildTools 'd8.bat'
$zipalign = Join-Path $buildTools 'zipalign.exe'
$apksigner = Join-Path $buildTools 'apksigner.bat'

# Detectar JDK (javac, jar, keytool)
$jdkCandidates = @(
    'C:\Program Files\Android\jdk',
    'C:\Program Files\Java',
    'C:\Program Files (x86)\Java',
    $env:JAVA_HOME
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_) }

$jarExe = $null
$javacExe = $null
$keytoolExe = $null

foreach ($cand in $jdkCandidates) {
    if (-not $jarExe) {
        $found = Get-ChildItem -Path $cand -Filter 'jar.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $jarExe = $found.FullName }
    }
    if (-not $javacExe) {
        $found = Get-ChildItem -Path $cand -Filter 'javac.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $javacExe = $found.FullName }
    }
    if (-not $keytoolExe) {
        $found = Get-ChildItem -Path $cand -Filter 'keytool.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $keytoolExe = $found.FullName }
    }
}

if (-not $jarExe) { throw 'No se encontró jar.exe del JDK.' }
if (-not $javacExe) { $javacExe = 'javac' }
if (-not $keytoolExe) { $keytoolExe = 'keytool' }

$jar = $jarExe
$keytool = $keytoolExe
$unsigned = Join-Path $build 'unsigned.apk'
$aligned = Join-Path $build 'aligned.apk'
$keystore = Join-Path $build 'androidtools-debug.keystore'

foreach ($required in @($androidJar, $aapt2, $d8, $zipalign, $apksigner)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "No se encontró: $required" }
}

Remove-Item -LiteralPath $classes,$dex,$generated -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $classes,$dex,$generated | Out-Null
Remove-Item -LiteralPath $unsigned,$aligned,$Output -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null

& $aapt2 compile --dir (Join-Path $src 'res') -o (Join-Path $build 'resources.zip')
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile falló.' }
& $aapt2 link -I $androidJar --manifest (Join-Path $src 'AndroidManifest.xml') `
    -R (Join-Path $build 'resources.zip') --java $generated -o $unsigned --auto-add-overlay
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link falló.' }

$java = Join-Path $src 'java\com\androidtools\receiver\ShareReceiverActivity.java'
& $javacExe -source 8 -target 8 -encoding UTF-8 -classpath $androidJar -d $classes $java
if ($LASTEXITCODE -ne 0) { throw 'javac falló.' }
& $d8 --lib $androidJar --min-api 23 --output $dex (Get-ChildItem -LiteralPath $classes -Recurse -Filter '*.class').FullName
if ($LASTEXITCODE -ne 0) { throw 'd8 falló.' }

& $jar uf $unsigned -C $dex classes.dex
if ($LASTEXITCODE -ne 0) { throw 'No se pudo incorporar classes.dex.' }
& $zipalign -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw 'zipalign falló.' }

if (-not (Test-Path -LiteralPath $keystore)) {
    & $keytool -genkeypair -keystore $keystore -storepass android -keypass android `
        -alias androidtools -keyalg RSA -keysize 2048 -validity 10000 `
        -dname 'CN=AndroidTools, OU=AndroidTools, O=AndroidTools, C=MX'
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el almacén de firma.' }
}
& $apksigner sign --ks $keystore --ks-pass pass:android --key-pass pass:android `
    --out $Output $aligned
if ($LASTEXITCODE -ne 0) { throw 'apksigner falló.' }
& $apksigner verify --verbose $Output
if ($LASTEXITCODE -ne 0) { throw 'La firma de la APK no pasó la verificación.' }
Write-Host "APK creada: $Output"
