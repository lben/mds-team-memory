# From the repository root: .\Update.ps1 [UAT|PROD] [--check]
& uv run --system-certs --python 3.12 "$PSScriptRoot/tools/client_cli.py" update @args
exit $LASTEXITCODE
