@echo off
uv run --python 3.12 "%~dp0tools\testdata.py" %*
exit /b %ERRORLEVEL%
