@echo off
title ANDROID TRANSFER
color 0B
echo ========================================
echo       ANDROID FILE TRANSFER
echo ========================================
echo.
echo Conectando...

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0android_transfer.ps1"
pause
