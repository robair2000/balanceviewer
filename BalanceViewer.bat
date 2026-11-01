@echo off
title BalanceViewer - close this window to stop
cd /d "%~dp0"
python server.py
if errorlevel 1 pause
