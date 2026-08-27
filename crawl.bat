@echo off
REM ============================================================================
REM  Captn crawler launcher (Windows)
REM
REM  Mirror an open-source GitHub repo's code locally as an AI-ready dataset.
REM
REM  Usage:
REM    crawl.bat owner/name
REM    crawl.bat owner/name --match ".*\.py$" --dest ./mirror
REM    crawl.bat owner/name --overwrite          (re-fetch, replacing existing files)
REM    crawl.bat owner/name --dry-run --json
REM
REM  All args after the repo are forwarded to tools\crawler_cli.py.
REM  Reads GITHUB_TOKEN from the environment if set (higher rate limit).
REM ============================================================================
cd /d "%~dp0"
python tools\crawler_cli.py %*
