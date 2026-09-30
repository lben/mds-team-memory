#!/usr/bin/env python3
"""Entry point for MDS Team Knowledge management commands.

    python manage.py create-admin
    python manage.py list-admins
    python manage.py reset-database
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

if __name__ == "__main__":
    if sys.argv[1:2] == ["test-data"]:
        from app.testdata import main
        raise SystemExit(main(sys.argv[2:]))
    from app.cli import main
    main()
