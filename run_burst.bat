@echo off
title Burst Download Manager
cd /d "%~dp0"
echo ========================================================
echo   Starting Burst Download Manager...
echo   Web UI will also be accessible at: http://127.0.0.1:59284
echo ========================================================
python backend\main.py
pause
