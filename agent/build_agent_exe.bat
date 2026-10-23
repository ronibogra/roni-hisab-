@echo off
title Build the Laila Group IT Agent .exe
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>&1 || (echo Install Python 3 first from https://www.python.org/downloads/ ^(tick "Add to PATH"^). & pause & exit /b 1)

echo Installing build tools (only on this build PC, one time)...
%PY% -m pip install --disable-pip-version-check -q pyinstaller psutil requests pystray Pillow || goto :fail

echo Building lailagent.exe ...
%PY% -m PyInstaller --noconfirm lailagent.spec || goto :fail

echo Assembling the ready-to-copy folder "LailaAgent_Deploy" ...
set "OUT=LailaAgent_Deploy"
rmdir /s /q "%OUT%" >nul 2>&1
mkdir "%OUT%"
copy /y dist\lailagent.exe "%OUT%\lailagent.exe" >nul
copy /y Install.bat "%OUT%\Install.bat" >nul
copy /y Uninstall.bat "%OUT%\Uninstall.bat" >nul
> "%OUT%\lailagent.ini" echo [agent]
>> "%OUT%\lailagent.ini" echo server = http://MONITOR-PC-IP:8080
>> "%OUT%\lailagent.ini" echo token  = PASTE-THE-AGENT-KEY-FROM-SETTINGS
> "%OUT%\READ_ME_FIRST.txt" echo 1. Open lailagent.ini and set the server address and the agent key
>> "%OUT%\READ_ME_FIRST.txt" echo    (from the monitor: Settings -^> IT agent).
>> "%OUT%\READ_ME_FIRST.txt" echo 2. Copy this whole LailaAgent_Deploy folder to each company PC.
>> "%OUT%\READ_ME_FIRST.txt" echo 3. On each PC, run Install.bat (no other software is needed).
>> "%OUT%\READ_ME_FIRST.txt" echo To remove it later, run Uninstall.bat on that PC.
echo.
echo ================================================================
echo Done. Your ready-to-deploy folder is:
echo    %~dp0%OUT%
echo Fill in lailagent.ini once, then copy that folder to every PC
echo and run Install.bat there. The PCs need nothing else installed.
echo ================================================================
pause
exit /b 0
:fail
echo.
echo Build failed. Make sure Python 3 is installed and you have internet.
pause
exit /b 1
