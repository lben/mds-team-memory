from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_admin, get_profile
from ..concepts import match_concepts
from ..db import get_db
from ..docstore import save_uploaded_document
from ..impact import mark_helped, notify, record_event
from ..knowledge import item_dict, process_after_save
from ..models import (
    AdminUser,
    ExpertiseMapping,
    KnowledgeItem,
    Profile,
    Relationship,
    Revision,
    utcnow,
)

router = APIRouter(prefix="/api", tags=["items"])


class CorrectionIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


def _get_item(db: Session, item_id: str, profile: Profile) -> KnowledgeItem:
    item = db.get(KnowledgeItem, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if item.visibility == "private" and item.author_profile_id != profile.id:
        raise HTTPException(404, "Item not found")
    return item


@router.post("/capture")
def capture(
    body: str = Form(""),
    file: UploadFile | None = None,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    body = body.strip()
    if not body and not file:
        raise HTTPException(400, "Write something or attach a document")
    document = save_uploaded_document(db, profile, file) if file else None
    result: dict = {"document_id": document.id if document else None, "item": None}
    corroboration = {"group_size": 1, "contributors": 1}
    if body:
        item = KnowledgeItem(
            kind="note",
            body=body,
            visibility="team",
            author_profile_id=profile.id,
            source_document_id=document.id if document else None,
        )
        db.add(item)
        db.commit()
        corroboration = process_after_save(db, item)
        result["item"] = item_dict(db, item, profile)
    result["corroboration"] = corroboration
    return result


@router.get("/items/{item_id}")
def item_detail(
    item_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    item = _get_item(db, item_id, profile)
    corrections = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.kind == "correction", KnowledgeItem.parent_id == item.id)
        .order_by(KnowledgeItem.created_at)
        .all()
    )
    revisions = (
        db.query(Revision).filter(Revision.item_id == item.id).order_by(Revision.created_at).all()
    )
    group_members = []
    if item.group_id:
        group_members = [
            item_dict(db, m, profile)
            for m in db.query(KnowledgeItem)
            .filter(KnowledgeItem.group_id == item.group_id)
            .order_by(KnowledgeItem.created_at)
            .all()
        ]
    concepts = match_concepts(db, (item.title or "") + " " + item.body)
    data = item_dict(db, item, profile)
    data["concepts"] = [{"id": c.id, "name": c.name} for c in concepts]
    data["corrections"] = [item_dict(db, c, profile) for c in corrections]
    data["revisions"] = [
        {"id": r.id, "correction_id": r.correction_id, "note": r.note, "created_at": r.created_at.isoformat() + "Z"}
        for r in revisions
    ]
    data["group_members"] = group_members
    # The scratchpad-origin link is visible only to the excerpt's owner.
    data["source_item_id"] = item.source_item_id if item.author_profile_id == profile.id else None
    return data


@router.post("/items/{item_id}/helped")
def helped(
    item_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    item = _get_item(db, item_id, profile)
    created, message = mark_helped(db, item, profile)
    if not created and message != "Already marked":
        raise HTTPException(400, message)
    return {"created": created, "detail": message}


@router.post("/items/{item_id}/endorse")
def endorse(
    item_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    item = _get_item(db, item_id, profile)
    if item.author_profile_id == profile.id:
        raise HTTPException(400, "You cannot endorse your own contribution")
    subject_text = (item.title or "") + " " + item.body
    parent = db.get(KnowledgeItem, item.parent_id) if item.parent_id else None
    if parent:
        subject_text += " " + (parent.title or "") + " " + parent.body
    concept_ids = [c.id for c in match_concepts(db, subject_text)]
    is_sme = (
        db.query(ExpertiseMapping)
        .filter(
            ExpertiseMapping.profile_id == profile.id,
            ExpertiseMapping.concept_id.in_(concept_ids or [""]),
        )
        .first()
        is not None
    )
    if not is_sme:
        raise HTTPException(403, "Only a mapped expert for this topic can endorse")
    created = record_event(
        db, "sme_endorsed", item.author_profile_id, f"sme:{profile.id}:{item.id}", profile.id, item.id
    )
    if created:
        notify(db, item.author_profile_id, "endorsed", "An expert endorsed your contribution.", item.id)
    db.commit()
    return {"created": created}


@router.post("/items/{item_id}/corrections")
def add_correction(
    item_id: str,
    payload: CorrectionIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    item = _get_item(db, item_id, profile)
    if item.kind == "correction":
        raise HTTPException(400, "Cannot correct a correction")
    correction = KnowledgeItem(
        kind="correction",
        body=payload.body.strip(),
        visibility=item.visibility,
        author_profile_id=profile.id,
        parent_id=item.id,
        correction_state="proposed",
    )
    db.add(correction)
    db.commit()
    if item.author_profile_id != profile.id:
        notify(
            db,
            item.author_profile_id,
            "correction",
            "A teammate proposed a correction to your contribution.",
            item.id,
        )
        db.commit()
    return item_dict(db, correction, profile)


@router.post("/corrections/{correction_id}/adopt")
def adopt_correction(
    correction_id: str,
    profile: Profile = Depends(get_profile),
    admin: AdminUser | None = Depends(get_admin),
    db: Session = Depends(get_db),
):
    correction = db.get(KnowledgeItem, correction_id)
    if not correction or correction.kind != "correction":
        raise HTTPException(404, "Correction not found")
    original = db.get(KnowledgeItem, correction.parent_id)
    if not original:
        raise HTTPException(404, "Corrected item not found")
    if not admin and original.author_profile_id != profile.id:
        raise HTTPException(403, "Only the original contributor or an admin can adopt a correction")
    if correction.correction_state != "adopted":
        correction.correction_state = "adopted"
        db.add(
            Revision(
                item_id=original.id,
                correction_id=correction.id,
                note="Correction adopted",
            )
        )
        original.updated_at = utcnow()
        db.commit()
    created = record_event(
        db,
        "correction_adopted",
        correction.author_profile_id,
        f"correction:{correction.id}",
        profile.id,
        correction.id,
    )
    if created and correction.author_profile_id != profile.id:
        notify(
            db,
            correction.author_profile_id,
            "correction_adopted",
            "Your correction was adopted.",
            original.id,
        )
    db.commit()
    return {"adopted": True, "impact_created": created}


@router.get("/items/{item_id}/relationships")
def item_relationships(
    item_id: str, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    _get_item(db, item_id, profile)
    rels = (
        db.query(Relationship)
        .filter(
            ((Relationship.src_kind == "item") & (Relationship.src_id == item_id))
            | ((Relationship.dst_kind == "item") & (Relationship.dst_id == item_id))
        )
        .all()
    )
    return [
        {
            "id": r.id,
            "src_kind": r.src_kind,
            "src_id": r.src_id,
            "rel_type": r.relationship_type.name,
            "dst_kind": r.dst_kind,
            "dst_id": r.dst_id,
            "evidence": r.evidence,
            "state": r.state,
        }
        for r in rels
    ]
