@echo off
rem Установка распознавания голосовых. Двойной клик — и ждём.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-voice.ps1" %*
echo.
pause
