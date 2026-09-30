# From the repository root: .\TestData.ps1 [UAT] <command> [options]
$env:UV_SYSTEM_CERTS = "true"
$env:UV_NATIVE_TLS = "true"
& uv run --python 3.12 "$PSScriptRoot/tools/testdata.py" @args
exit $LASTEXITCODE
