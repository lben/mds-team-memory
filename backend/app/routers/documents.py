from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..docstore import document_dict, save_uploaded_document
from ..knowledge import item_dict, process_after_save
from ..models import Document, DocumentPassage, KnowledgeItem, Profile

router = APIRouter(prefix="/api", tags=["documents"])


class SharePassageIn(BaseModel):
    comment: str | None = None


@router.post("/documents")
def upload_document(
    file: UploadFile, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    document = save_uploaded_document(db, profile, file)
    return document_dict(document)


@router.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    documents = db.query(Document).order_by(Document.uploaded_at.desc()).all()
    return [document_dict(d) for d in documents]


@router.get("/documents/{document_id}")
def document_detail(document_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")
    return document_dict(document, with_passages=True)


@router.get("/documents/{document_id}/file")
def download_document(document_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document or not Path(document.stored_path).exists():
        raise HTTPException(404, "Document not found")
    return FileResponse(document.stored_path, filename=document.filename)


@router.post("/passages/{passage_id}/share")
def share_passage(
    passage_id: str,
    payload: SharePassageIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    passage = db.get(DocumentPassage, passage_id)
    if not passage:
        raise HTTPException(404, "Passage not found")
    body = passage.text
    if payload.comment and payload.comment.strip():
        body = payload.comment.strip() + "\n\n" + body
    item = KnowledgeItem(
        kind="excerpt",
        body=body,
        visibility="team",
        author_profile_id=profile.id,
        source_document_id=passage.document_id,
        source_passage_id=passage.id,
    )
    db.add(item)
    db.commit()
    corroboration = process_after_save(db, item)
    return {"item": item_dict(db, item, profile), "corroboration": corroboration}
