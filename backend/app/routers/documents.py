from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..docstore import document_dict, save_uploaded_document
from ..impact import shared_count
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
    return document_dict(document, profile=profile)


@router.get("/documents")
def list_documents(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    documents = db.query(Document).order_by(Document.uploaded_at.desc()).all()
    return [document_dict(d, profile=profile) for d in documents]


@router.get("/documents/{document_id}")
def document_detail(
    document_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")
    return document_dict(document, with_passages=True, profile=profile)


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    """Remove a document you uploaded, and the file behind it.

    Excerpts a teammate chose to share from it stay: they are team knowledge in
    their own right. Their link back to the source is cleared rather than left
    pointing at a file that no longer exists.
    """
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if document.uploader_profile_id != profile.id:
        raise HTTPException(403, "Only the person who uploaded this can delete it")

    passage_ids = [p.id for p in document.passages]
    shared = db.query(KnowledgeItem).filter(KnowledgeItem.source_document_id == document.id).all()
    for item in shared:
        item.source_document_id = None
        item.source_passage_id = None
    db.flush()

    stored = Path(document.stored_path)
    db.delete(document)
    db.commit()
    # The row is gone whatever happens to the file; a missing file must not
    # leave a document listed that nothing can open.
    stored.unlink(missing_ok=True)
    return {"deleted": True, "kept_shared_excerpts": len(shared), "passages_removed": len(passage_ids)}


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
    return {
        "item": item_dict(db, item, profile),
        "corroboration": corroboration,
        "shared_total": shared_count(db, profile.id),
    }
