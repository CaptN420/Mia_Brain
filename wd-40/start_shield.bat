@echo off
REM wd-40 shield autostart launcher (LOGON trigger, runs as YOU).
REM Uses pythonw.exe (windowless) + redirects output to a log file so the
REM daemon survives detached (no console to be reaped, no broken stdout pipe).
REM
REM Stop it:  write wd-40\stop.flag   (e.g. copy nul > wd-40\stop.flag)
REM           daemon exits within ~5s.
REM
REM Watch it:  type wd-40\logs\daemon.log

set "WD=%~dp0"
set "PY=%LOCALAPPDATA%\Python\bin\pythonw.exe"
if not exist "%PY%" set "PY=C:\Users\macel\AppData\Local\Python\bin\pythonw.exe"
if not exist "%PY%" (
    echo [wd-40] ERROR: pythonw not found > "%WD%logs\startup_error.log"
    exit /b 1
)

cd /d "%WD%"
"%PY%" "%WD%daemon.py" --interval 300 >> "%WD%logs\daemon.log" 2>&1
exit /b %errorlevel%
