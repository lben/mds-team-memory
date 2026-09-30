@echo off
setlocal
uv run --system-certs --python 3.12 "%~dp0tools\update.py" %*
exit /b %ERRORLEVEL%
