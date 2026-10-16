@echo off
title Android ADB Tools - Control Center (Flet)
cd /d "%~dp0"
echo ============================================================
echo         ANDROID ADB TOOLS - CONTROL CENTER (FLET)
echo ============================================================
echo.
echo Iniciando interfaz de escritorio Flet...
echo.
python -m ui.main %*
