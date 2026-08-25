import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from . import config
from .concepts import tag_subject
from .extraction import extract_passages
from .models import Document, DocumentPassage, Profile

_MAGIC = {
    ".pdf": b"%PDF",
    ".docx": b"PK\x03\x04",  # docx is a zip container
}


def save_uploaded_document(db: Session, profile: Profile, upload: UploadFile) -> Document:
    """Validate, store and extract an uploaded file. Raises HTTPException on rejection."""
    filename = Path(upload.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(400, "Only PDF, DOCX, TXT and Markdown files are supported")
    content = upload.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File is too large")
    if not content:
        raise HTTPException(400, "File is empty")
    magic = _MAGIC.get(extension)
    if magic and not content.startswith(magic):
        raise HTTPException(400, "File content does not match its extension")

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.-]", "_", filename) or "upload"
    stored = config.UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    stored.write_bytes(content)

    document = Document(
        filename=filename, stored_path=str(stored), uploader_profile_id=profile.id
    )
    try:
        passages = extract_passages(stored, extension)
    except ValueError as exc:
        stored.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    db.add(document)
    db.flush()
    for ord_no, p in enumerate(passages):
        passage = DocumentPassage(
            document_id=document.id, ord=ord_no, text=p["text"], locator=p["locator"]
        )
        db.add(passage)
        db.flush()
        tag_subject(db, "passage", passage.id, p["text"])
    db.commit()
    return document


def document_dict(document: Document, with_passages: bool = False) -> dict:
    data = {
        "id": document.id,
        "filename": document.filename,
        "uploader": document.uploader.label,
        "uploaded_at": document.uploaded_at.isoformat() + "Z",
        "status": document.status,
        "passage_count": len(document.passages),
    }
    if with_passages:
        data["passages"] = [
            {"id": p.id, "ord": p.ord, "text": p.text, "locator": p.locator}
            for p in document.passages
        ]
    return data
