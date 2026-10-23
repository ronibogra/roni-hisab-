@echo off
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=pyw -3") || (set "PY=pythonw")
start "" %PY% lailagent.py
