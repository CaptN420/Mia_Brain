@echo off
REM wd-40 tray controller launcher (windowless). View-only control panel.
REM The SHIELD itself is started/stopped separately (start_shield.bat /
REM wd-40\stop.flag). This only adds a taskbar icon + menus.
set "WD=%~dp0"
set "PY=%LOCALAPPDATA%\Python\bin\pythonw.exe"
if not exist "%PY%" set "PY=C:\Users\macel\AppData\Local\Python\bin\pythonw.exe"
if not exist "%PY%" (
    echo [wd-40] ERROR: pythonw not found > "%WD%logs\tray_startup_error.log"
    exit /b 1
)
cd /d "%WD%"
start "" "%PY%" "%WD%tray.py"
exit /b 0
