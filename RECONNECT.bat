@echo off
title ANDROID RECONNECT
color 0E
echo ========================================
echo       ANDROID QUICK RECONNECT
echo ========================================
echo.
echo Reconectando...

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "android_reconnect.ps1"
pause