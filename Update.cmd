@echo off
setlocal
uv run --system-certs --python 3.12 "%~dp0tools\client_cli.py" update %*
exit /b %ERRORLEVEL%
