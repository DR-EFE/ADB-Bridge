[CmdletBinding()]
param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
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
$androidJar = Join-Path $SdkRoot 'platforms\android-35\android.jar'
$buildTools = Join-Path $SdkRoot 'build-tools\36.1.0'
$aapt2 = Join-Path $buildTools 'aapt2.exe'
$d8 = Join-Path $buildTools 'd8.bat'
$zipalign = Join-Path $buildTools 'zipalign.exe'
$apksigner = Join-Path $buildTools 'apksigner.bat'
$jdkTools = @(Get-ChildItem -Path 'C:\Program Files\Java' -Filter 'jar.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($jdkTools.Count -eq 0) { throw 'No se encontró jar.exe del JDK.' }
$jar = $jdkTools[0].FullName
$keytool = Join-Path (Split-Path -Parent $jar) 'keytool.exe'
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
& javac -source 8 -target 8 -encoding UTF-8 -classpath $androidJar -d $classes $java
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
