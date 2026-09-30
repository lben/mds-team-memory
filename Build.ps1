# From the repository root: .\Build.ps1 [--force]
& uv run --system-certs --python 3.12 "$PSScriptRoot/tools/build_ui.py" @args
exit $LASTEXITCODE
