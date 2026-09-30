@echo off
setlocal
set "UV_SYSTEM_CERTS=true"
set "UV_NATIVE_TLS=true"
uv run --python 3.12 "%~dp0tools\testdata.py" %*
exit /b %ERRORLEVEL%
