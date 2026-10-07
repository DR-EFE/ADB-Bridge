@echo off
title ANDROID SETUP
color 0A
echo ========================================
echo       ANDROID USB to WiFi SETUP
echo ========================================
echo.
echo INSTRUCCIONES:
echo 1. Conecta el celular por USB
echo 2. Asegura que Depuracion USB este activada
echo 3. WiFi del celular activado
echo.
pause

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0setup.ps1"
pause
