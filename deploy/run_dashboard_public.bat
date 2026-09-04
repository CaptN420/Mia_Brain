# ============================================================================
# Captn public demo launcher (server / VPS)
#
# Differences from run_dashboard.bat (dev):
#   - binds 0.0.0.0 so the reverse proxy can reach it
#   - REQUIRES a token (refuses to start without one)
# ============================================================================

@echo off
cd /d "%~dp0.."

REM --- Passphrase is mandatory for public mode -------------------------------
if "%CAPTN_AUTH_PASSPHRASE%"=="" (
    if exist ".captn\auth_passphrase" (
        echo Using passphrase from .captn\auth_passphrase
    ) else (
        echo ERROR: no access passphrase configured.
        echo   Generate one with:
        echo     python -c "import secrets; print(secrets.token_urlsafe(32))"
        echo   then either:
        echo     set CAPTN_AUTH_PASSPHRASE=<the-passphrase>
        echo   or save it to .captn\auth_passphrase
        pause
        exit /b 1
    )
)

set VENV_PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe
if not exist "%VENV_PY%" set VENV_PY=python

echo Starting Captn PUBLIC demo on 0.0.0.0:8501 (behind reverse proxy)
"%VENV_PY%" -m streamlit run captn\dashboard\app.py --server.headless true --server.address 0.0.0.0 --server.port 8501
