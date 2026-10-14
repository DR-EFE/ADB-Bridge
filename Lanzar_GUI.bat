@echo off
title Android ADB Tools - Control Center
cd /d "%~dp0"

echo ============================================================
echo         ANDROID ADB TOOLS - CONTROL CENTER (GUI)
echo ============================================================
echo.
echo Iniciando interfaz grafica...
echo.

powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -STA -File "%~dp0AndroidTools.GUI.ps1"

if errorlevel 1 (
    echo.
    echo La GUI termino con un error.
    pause
)
