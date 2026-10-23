@echo off
title Laila Group IT Agent - setup
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
echo Installing the IT agent (hardware and performance reporting)...
%PY% -m pip install --disable-pip-version-check -q psutil requests pystray Pillow || goto :fail
if not exist lailagent.ini %PY% lailagent.py
echo.
echo Now edit lailagent.ini (server address + agent key from Settings), then run start_agent.bat
echo To start automatically at logon, run install_agent_autostart.bat
pause
exit /b 0
:fail
echo Install failed. Check internet and that Python 3 is installed.
pause
