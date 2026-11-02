@echo off
REM Keeps the desktop balance panel fresh: re-renders every 10 minutes.
REM Started silently at logon by the "balanceviewer-loop" scheduled task.

cd /d "%~dp0"
pythonw wallpaper.py --loop 600 >> wallpaper.log 2>&1
