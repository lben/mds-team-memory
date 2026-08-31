#!/usr/bin/env python3
"""Entry point for MDS Team Knowledge management commands.

    python manage.py create-admin
    python manage.py list-admins
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
