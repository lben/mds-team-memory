# From the repository root: .\TestData.ps1 [UAT] <command> [options]
& uv run --system-certs --python 3.12 "$PSScriptRoot/tools/testdata.py" @args
exit $LASTEXITCODE
