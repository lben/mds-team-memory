# From the repository root: .\Update.ps1 [UAT|PROD] [--check]
$env:UV_SYSTEM_CERTS = "true"
$env:UV_NATIVE_TLS = "true"
& uv run --python 3.12 "$PSScriptRoot/tools/update.py" @args
exit $LASTEXITCODE
