import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("MDS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "mds.sqlite3"
DATABASE_URL = os.environ.get("MDS_DATABASE_URL", f"sqlite:///{DB_PATH}")

SECURE_COOKIES = os.environ.get("MDS_SECURE_COOKIES", "0") == "1"
SIMILARITY_THRESHOLD = float(os.environ.get("MDS_SIMILARITY_THRESHOLD", "0.95"))
COOCCURRENCE_MIN = int(os.environ.get("MDS_COOCCURRENCE_MIN", "1"))
MAX_UPLOAD_BYTES = int(os.environ.get("MDS_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
ADMIN_SESSION_HOURS = int(os.environ.get("MDS_ADMIN_SESSION_HOURS", "12"))

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
