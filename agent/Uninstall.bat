@echo off
title Remove Laila Group IT Agent
schtasks /end /tn "Laila Group IT Agent" >nul 2>&1
schtasks /delete /tn "Laila Group IT Agent" /f >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LailaAgent.lnk" >nul 2>&1
taskkill /im lailagent.exe /f >nul 2>&1
rmdir /s /q "%ProgramData%\LailaGroup\Agent" >nul 2>&1
echo The IT agent has been removed from this PC.
pause
