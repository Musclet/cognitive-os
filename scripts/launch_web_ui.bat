@echo off
title Cognitive OS
cd /d "%~dp0.."
python scripts\launch_web_ui.pyw
echo.
echo Press any key to close this window...
pause >nul
