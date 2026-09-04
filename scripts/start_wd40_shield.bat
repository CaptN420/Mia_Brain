@echo off
:: wd-40 Windows Shield Launcher
echo == wd-40 Windows Shield ==
echo.
:: Check if already running
if exist wd-40\.shield.pid (
    for /f "tokens=*" %%P in (wd-40\.shield.pid) do (
        tasklist %%P | find >nul && echo Shield already running with PID %%P && goto :eof
    )
)
echo Starting wd-40 daemon...
python wd-40/daemon.py --interval 60
echo.
echo Starting tray controller...
pythonw wd-40	ray.py
echo.
echo wd-40 Shield Active.
echo Use right-click tray icon or run wd-40\center.py status to check.
echo To stop: del wd-40\stop.flag
pause
