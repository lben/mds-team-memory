from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    clear_admin_session,
    create_admin_session,
    get_admin,
    hash_password,
    require_admin,
    verify_password,
)
from ..concepts import match_concepts
from ..db import get_db
from ..models import (
    AdminUser,
    Concept,
    ConceptAlias,
    ExpertiseMapping,
    ItemConcept,
    KnowledgeItem,
    Profile,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CredentialsIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class ConceptIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = []


class MappingIn(BaseModel):
    profile_id: str
    concept_id: str


@router.get("/state")
def state(admin: AdminUser | None = Depends(get_admin), db: Session = Depends(get_db)):
    needs_setup = db.query(AdminUser).first() is None
    return {
        "needs_setup": needs_setup,
        "logged_in": admin is not None,
        "username": admin.username if admin else None,
    }


@router.post("/setup")
def setup(payload: CredentialsIn, response: Response, db: Session = Depends(get_db)):
    if db.query(AdminUser).first() is not None:
        raise HTTPException(403, "Setup is already complete")
    admin = AdminUser(username=payload.username.strip(), password_hash=hash_password(payload.password))
    db.add(admin)
    db.commit()
    create_admin_session(db, response, admin)
    return {"ok": True, "username": admin.username}


@router.post("/login")
def login(payload: CredentialsIn, response: Response, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter_by(username=payload.username.strip()).first()
    if not admin or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(401, "Invalid username or password")
    create_admin_session(db, response, admin)
    return {"ok": True, "username": admin.username}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    clear_admin_session(request, response, db)
    return {"ok": True}


@router.post("/admins", dependencies=[Depends(require_admin)])
def add_admin(payload: CredentialsIn, db: Session = Depends(get_db)):
    admin = AdminUser(username=payload.username.strip(), password_hash=hash_password(payload.password))
    db.add(admin)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That username already exists")
    return {"ok": True, "username": admin.username}


@router.get("/admins", dependencies=[Depends(require_admin)])
def list_admins(db: Session = Depends(get_db)):
    return [{"id": a.id, "username": a.username} for a in db.query(AdminUser).order_by(AdminUser.username)]


@router.get("/concepts", dependencies=[Depends(require_admin)])
def list_concepts(db: Session = Depends(get_db)):
    return [
        {"id": c.id, "name": c.name, "aliases": [a.alias for a in c.aliases]}
        for c in db.query(Concept).order_by(Concept.name).all()
    ]


@router.post("/concepts", dependencies=[Depends(require_admin)])
def create_concept(payload: ConceptIn, db: Session = Depends(get_db)):
    concept = Concept(name=payload.name.strip())
    db.add(concept)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That concept already exists")
    for alias in payload.aliases:
        alias = alias.strip().lower()
        if alias:
            db.add(ConceptAlias(concept_id=concept.id, alias=alias))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "An alias is already used by another concept")
    _retag_existing(db, concept)
    return {"id": concept.id, "name": concept.name, "aliases": [a.alias for a in concept.aliases]}


def _retag_existing(db: Session, concept: Concept) -> None:
    """Tag already-saved team items with a newly created concept (deterministic backfill)."""
    from ..concepts import tag_subject

    aliases = [concept.name.lower()] + [a.alias for a in concept.aliases]
    items = db.query(KnowledgeItem).filter(KnowledgeItem.visibility == "team").all()
    for item in items:
        low = ((item.title or "") + " " + item.body).lower()
        if any(a in low for a in aliases):
            tag_subject(db, "item", item.id, (item.title or "") + " " + item.body)
    db.commit()


@router.delete("/concepts/{concept_id}", dependencies=[Depends(require_admin)])
def delete_concept(concept_id: str, db: Session = Depends(get_db)):
    concept = db.get(Concept, concept_id)
    if not concept:
        raise HTTPException(404, "Concept not found")
    db.query(ItemConcept).filter(ItemConcept.concept_id == concept_id).delete()
    db.delete(concept)
    db.commit()
    return {"ok": True}


@router.get("/profiles", dependencies=[Depends(require_admin)])
def list_profiles(db: Session = Depends(get_db)):
    profiles = db.query(Profile).order_by(Profile.created_at).all()
    return [{"id": p.id, "label": p.label, "verified": False} for p in profiles]


@router.get("/expertise", dependencies=[Depends(require_admin)])
def list_mappings(db: Session = Depends(get_db)):
    mappings = db.query(ExpertiseMapping).all()
    by_profile: dict[str, dict] = {}
    for m in mappings:
        entry = by_profile.setdefault(
            m.profile_id, {"profile_id": m.profile_id, "label": m.profile.label, "areas": []}
        )
        entry["areas"].append({"mapping_id": m.id, "concept_id": m.concept_id, "name": m.concept.name})
    for entry in by_profile.values():
        entry["areas"].sort(key=lambda a: a["name"])
    return sorted(by_profile.values(), key=lambda e: e["label"])


@router.post("/expertise", dependencies=[Depends(require_admin)])
def add_mapping(payload: MappingIn, db: Session = Depends(get_db)):
    if not db.get(Profile, payload.profile_id):
        raise HTTPException(404, "Profile not found")
    if not db.get(Concept, payload.concept_id):
        raise HTTPException(404, "Concept not found")
    mapping = ExpertiseMapping(profile_id=payload.profile_id, concept_id=payload.concept_id)
    db.add(mapping)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That mapping already exists")
    return {"mapping_id": mapping.id}


@router.delete("/expertise/{mapping_id}", dependencies=[Depends(require_admin)])
def delete_mapping(mapping_id: str, db: Session = Depends(get_db)):
    mapping = db.get(ExpertiseMapping, mapping_id)
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    db.delete(mapping)
    db.commit()
    return {"ok": True}


@router.get("/routing-preview", dependencies=[Depends(require_admin)])
def routing_preview(q: str, db: Session = Depends(get_db)):
    concepts = match_concepts(db, q)
    experts = (
        db.query(ExpertiseMapping)
        .filter(ExpertiseMapping.concept_id.in_([c.id for c in concepts] or [""]))
        .all()
    )
    return {
        "detected": [c.name for c in concepts],
        "experts": sorted({e.profile.label for e in experts}),
    }
