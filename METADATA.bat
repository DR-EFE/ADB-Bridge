@echo off
title ANDROID PHOTO METADATA & FORENSIC MAP
color 0B
echo ======================================================================
echo    ANDROID TOOLS - EXTRACCION Y ANALISIS DE METADATOS FOTOGRAFICOS
echo    Analisis Geoespacial EXIF / GPS, Clusters y Dashboard Forense
echo ======================================================================
echo.

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0android_photo_metadata.ps1" %*
pause

