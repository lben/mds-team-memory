# From the repository root: .\TestData.ps1 [UAT] <command> [options]
& uv run --system-certs --python 3.12 "$PSScriptRoot/tools/client_cli.py" testdata @args
exit $LASTEXITCODE
