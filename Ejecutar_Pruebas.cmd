@echo off
title Android ADB Tools - Ejecutor de Pruebas
cd /d "%~dp0"
echo ============================================================
echo         ANDROID ADB TOOLS - SUITE DE PRUEBAS
echo ============================================================
echo.
echo Ejecutando pruebas unitarias y de integridad...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tests\Run-Tests.ps1"
echo.
pause
