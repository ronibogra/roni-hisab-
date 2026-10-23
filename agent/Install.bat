@echo off
title Install Laila Group IT Agent
cd /d "%~dp0"
if not exist lailagent.exe (
    echo lailagent.exe was not found in this folder.
    echo Copy this whole folder from the monitor PC and run Install.bat again.
    pause & exit /b 1
)
if not exist lailagent.ini (
    echo lailagent.ini was not found. It must contain the server address and agent key.
    pause & exit /b 1
)
set "DEST=%ProgramData%\LailaGroup\Agent"
mkdir "%DEST%" >nul 2>&1
copy /y lailagent.exe "%DEST%\lailagent.exe" >nul
copy /y lailagent.ini "%DEST%\lailagent.ini" >nul

rem start automatically at logon
schtasks /create /tn "Laila Group IT Agent" /tr "\"%DEST%\lailagent.exe\"" /sc onlogon /rl LIMITED /f >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut([Environment]::GetFolderPath('Startup')+'\LailaAgent.lnk'); $s.TargetPath='%DEST%\lailagent.exe'; $s.Save()" >nul 2>&1
)
start "" "%DEST%\lailagent.exe"
echo.
echo Done. The IT agent is installed and running (look for the tray icon).
echo It will start automatically when this user logs in.
pause
exit /b 0
