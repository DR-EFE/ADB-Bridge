@echo off
title Android ADB Tools - Panel Grafico
echo ============================================================
echo         ANDROID ADB TOOLS - CONTROL CENTER (GUI)
echo ============================================================
echo.
echo Iniciando interfaz grafica moderna...
echo.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0AndroidTools.GUI.ps1"
exit
