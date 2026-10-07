@echo off
title ANDROID SETUP
color 0A
echo ========================================
echo       ANDROID USB to WiFi SETUP
echo ========================================
echo.
echo INSTRUCCIONES:
echo 1. Para Wireless Debugging, activa Depuracion inalambrica
echo 2. Para TCP/IP 5555 legado, conecta y autoriza el USB
echo 3. Manten la PC y el telefono en la misma red WiFi
echo.
pause

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0setup.ps1"
pause
