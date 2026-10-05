@echo off
title ANDROID TRANSFER
color 0B
echo ========================================
echo       ANDROID FILE TRANSFER
echo ========================================
echo.
echo Conectando...

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "android_transfer.ps1"
pause