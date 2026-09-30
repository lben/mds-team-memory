@echo off
uv run --system-certs --python 3.12 "%~dp0tools\build_ui.py" %*
exit /b %ERRORLEVEL%
