# From the repository root: .\Update.ps1 [UAT|PROD] [--check]
& uv run --python 3.12 "$PSScriptRoot/tools/update.py" @args
exit $LASTEXITCODE
