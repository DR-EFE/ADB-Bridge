@echo off
title ANDROID RECONNECT
color 0E
echo ========================================
echo       ANDROID QUICK RECONNECT
echo ========================================
echo.
echo Reconectando...

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0android_reconnect.ps1"
pause
