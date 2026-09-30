@echo off
setlocal
uv run --system-certs --python 3.12 "%~dp0tools\testdata.py" %*
exit /b %ERRORLEVEL%
