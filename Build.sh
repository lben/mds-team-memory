#!/usr/bin/env sh
exec uv run --system-certs --python 3.12 "$(dirname "$0")/tools/build_ui.py" "$@"
