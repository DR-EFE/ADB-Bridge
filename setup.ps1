# android_setup.ps1 - Version simple
Write-Host "=== ANDROID SETUP ==="
adb devices
adb tcpip 5555
$ip = adb shell "ip route get 1 | awk \"{print `$NF}\""
Write-Host "IP: $ip"
adb connect "${ip}:5555"
Write-Host "Listo! Desconecta USB"
pause
