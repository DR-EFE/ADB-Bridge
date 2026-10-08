@echo off
title Android ADB Tools - Control Center
cd /d "%~dp0"
echo ============================================================
echo         ANDROID ADB TOOLS - CONTROL CENTER (GUI)
echo ============================================================
echo.
echo Iniciando interfaz grafica moderna...
echo.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0AndroidTools.GUI.ps1"
exit
