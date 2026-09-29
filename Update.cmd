@echo off
uv run --python 3.12 "%~dp0tools\update.py" %*
exit /b %ERRORLEVEL%
