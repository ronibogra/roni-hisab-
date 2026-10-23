@echo off
title Laila Group IT Agent - run at logon
cd /d "%~dp0"
where py >nul 2>&1 && (set "PYW=%LOCALAPPDATA%\Programs\Python\pythonw.exe") 
set "PYW=pythonw"
for /f "delims=" %%p in ('where pythonw 2^>nul') do set "PYW=%%p"
schtasks /create /tn "Laila Group IT Agent" /tr "\"%PYW%\" \"%~dp0lailagent.py\"" /sc onlogon /rl LIMITED /f >nul && (
  echo The agent will start automatically when this user logs in.
) || echo Could not create the task.
schtasks /run /tn "Laila Group IT Agent" >nul 2>&1
pause
