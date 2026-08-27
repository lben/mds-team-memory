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
from ..concepts import match_concepts, normalize_term, retag_everything
from ..db import get_db
from ..models import (
    AdminUser,
    Concept,
    ConceptTerm,
    ExpertiseMapping,
    Profile,
    Relationship,
    RelationshipType,
)
from ..relationships import type_usage

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


class ConceptUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = []


class RelationshipTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.get("/state")
def state(admin: AdminUser | None = Depends(get_admin)):
    """Whether this browser holds an admin session. Deliberately reveals nothing
    about whether admin accounts exist — accounts are created on the server with
    `python manage.py create-admin`."""
    return {"logged_in": admin is not None, "username": admin.username if admin else None}


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


def _concept_dict(concept: Concept) -> dict:
    return {"id": concept.id, "name": concept.name, "aliases": concept.aliases}


def _set_terms(db: Session, concept: Concept, name: str, aliases: list[str]) -> None:
    """Replace a concept's vocabulary, rejecting words another concept owns."""
    # The canonical term is the concept's identity, so it must exist even when
    # aliases would otherwise make the term set non-empty.
    canonical_term = normalize_term(name)
    if not canonical_term:
        raise HTTPException(400, "A concept needs a name")
    wanted: dict[str, str] = {}
    for display in [name, *aliases]:
        term = normalize_term(display)
        if term:
            wanted.setdefault(term, display.strip())

    taken = (
        db.query(ConceptTerm)
        .filter(ConceptTerm.term.in_(list(wanted)), ConceptTerm.concept_id != concept.id)
        .first()
    )
    if taken:
        owner = db.get(Concept, taken.concept_id)
        raise HTTPException(
            400, f"'{taken.display}' is already used by the concept '{owner.name if owner else '?'}'"
        )

    db.query(ConceptTerm).filter(ConceptTerm.concept_id == concept.id).delete()
    db.flush()
    for term, display in wanted.items():
        db.add(
            ConceptTerm(
                concept_id=concept.id,
                term=term,
                display=display,
                is_canonical=term == canonical_term,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That word is already used by another concept")
    db.refresh(concept)


@router.get("/concepts", dependencies=[Depends(require_admin)])
def list_concepts(db: Session = Depends(get_db)):
    concepts = db.query(Concept).all()
    return [_concept_dict(c) for c in sorted(concepts, key=lambda c: c.name.lower())]


@router.post("/concepts", dependencies=[Depends(require_admin)])
def create_concept(payload: ConceptIn, db: Session = Depends(get_db)):
    concept = Concept()
    db.add(concept)
    db.flush()
    _set_terms(db, concept, payload.name, payload.aliases)
    retag_everything(db)
    return _concept_dict(concept)


@router.put("/concepts/{concept_id}", dependencies=[Depends(require_admin)])
def update_concept(concept_id: str, payload: ConceptUpdateIn, db: Session = Depends(get_db)):
    concept = db.get(Concept, concept_id)
    if not concept:
        raise HTTPException(404, "Concept not found")
    _set_terms(db, concept, payload.name, payload.aliases)
    # Renaming or re-aliasing changes what the text matches, so rebuild the tags.
    retag_everything(db)
    return _concept_dict(concept)


@router.delete("/concepts/{concept_id}", dependencies=[Depends(require_admin)])
def delete_concept(concept_id: str, db: Session = Depends(get_db)):
    concept = db.get(Concept, concept_id)
    if not concept:
        raise HTTPException(404, "Concept not found")
    # Tags, terms and expertise mappings cascade; concept links are polymorphic
    # by design and are removed here.
    db.query(Relationship).filter(
        Relationship.src_kind == "concept",
        (Relationship.src_id == concept_id) | (Relationship.dst_id == concept_id),
    ).delete()
    db.delete(concept)
    db.commit()
    return {"ok": True}


@router.post("/relationship-types", dependencies=[Depends(require_admin)])
def create_relationship_type(payload: RelationshipTypeIn, db: Session = Depends(get_db)):
    rtype = RelationshipType(name=payload.name.strip(), is_builtin=False)
    db.add(rtype)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That relationship type already exists")
    return {"id": rtype.id, "name": rtype.name, "is_builtin": False, "usage": 0}


@router.put("/relationship-types/{type_id}", dependencies=[Depends(require_admin)])
def rename_relationship_type(
    type_id: str, payload: RelationshipTypeIn, db: Session = Depends(get_db)
):
    rtype = db.get(RelationshipType, type_id)
    if not rtype:
        raise HTTPException(404, "Relationship type not found")
    if rtype.is_builtin:
        raise HTTPException(400, f"'{rtype.name}' is built in and cannot be renamed")
    rtype.name = payload.name.strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That relationship type already exists")
    return {"id": rtype.id, "name": rtype.name, "usage": type_usage(db, rtype.id)}


@router.delete("/relationship-types/{type_id}", dependencies=[Depends(require_admin)])
def delete_relationship_type(type_id: str, db: Session = Depends(get_db)):
    rtype = db.get(RelationshipType, type_id)
    if not rtype:
        raise HTTPException(404, "Relationship type not found")
    if rtype.is_builtin:
        raise HTTPException(400, f"'{rtype.name}' is built in and cannot be deleted")
    usage = type_usage(db, type_id)
    if usage:
        raise HTTPException(
            400,
            f"'{rtype.name}' is used by {usage} link{'s' if usage != 1 else ''}. "
            "Change or remove those links first.",
        )
    db.delete(rtype)
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
