@echo off
REM ============================================================================
REM  Captn dashboard launcher (Windows)
REM
REM  Starts the Streamlit project workspace (includes the Open-Source Code
REM  Crawler panel + the new "Feed into learning corpus" self-code-learning
REM  button + the Raw->JSON progress bar).
REM
REM  Usage:
REM    run_dashboard.bat                 (serves at http://localhost:8501)
REM    run_dashboard.bat --server.port 9000
REM
REM  Requires streamlit + pandas (installed in the Hermes venv).
REM
REM  SAFETY: any process already bound to the chosen port is killed first,
REM  so a stale/orphaned dashboard (serving OLD code) can never shadow the
REM  fresh one we are about to start.
REM ============================================================================

cd /d "%~dp0"

set VENV_PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe
if not exist "%VENV_PY%" (
    echo ERROR: venv python not found at %VENV_PY%
    echo        Install streamlit with: "%VENV_PY%" -m pip install streamlit pandas
    pause
    exit /b 1
)

REM --- Resolve the port (default 8501, overridable via --server.port N) ---
set PORT=8501
set ARGS=%*
:parse_args
if "%1"=="" goto :done_args
if /I "%1"=="--server.port" (
    if not "%2"=="" set PORT=%2
)
shift
goto :parse_args
:done_args

REM --- Kill anything already bound to that port (orphan cleanup) ---
echo Checking for a process already using port %PORT% ...
for /f "tokens=5" %%P in ('netstat -ano -p TCP ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo   killing orphaned PID %%P on port %PORT%
    taskkill /F /PID %%P >nul 2>&1
)
REM give the OS a moment to release the socket
ping -n 2 127.0.0.1 >nul 2>&1

REM --- Launch the fresh dashboard ---
REM E-05: bind to 127.0.0.1 only - never expose this unauthenticated panel
REM on the network. Remove --server.address at your own risk.
echo Starting Captn dashboard on http://localhost:%PORT%
"%VENV_PY%" -m streamlit run captn\dashboard\app.py --server.headless true --server.address 127.0.0.1 --server.port %PORT% %ARGS%
