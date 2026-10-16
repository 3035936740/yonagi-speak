@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-QQ-Group-Bot.ps1" -WebUI %*
pause
