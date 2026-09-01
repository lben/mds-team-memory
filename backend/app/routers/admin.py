from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import hash_password, require_admin
from ..concepts import match_concepts, normalize_term, retag_everything
from ..db import get_db
from ..models import (
    Account,
    Concept,
    ImpactEvent,
    ItemConcept,
    ConceptTerm,
    ExpertiseMapping,
    Profile,
    Relationship,
    RelationshipType,
)
from ..relationships import refresh_for_concept, type_usage

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


@router.post("/admins", dependencies=[Depends(require_admin)])
def add_admin(payload: CredentialsIn, db: Session = Depends(get_db)):
    admin = Account(
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        is_admin=True,
    )
    db.add(admin)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That username already exists")
    return {"ok": True, "username": admin.username}


@router.get("/admins", dependencies=[Depends(require_admin)])
def list_admins(db: Session = Depends(get_db)):
    admins = db.query(Account).filter_by(is_admin=True).order_by(Account.username)
    return [{"id": a.id, "username": a.username} for a in admins]


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
    refresh_for_concept(db, concept.id)
    return _concept_dict(concept)


@router.put("/concepts/{concept_id}", dependencies=[Depends(require_admin)])
def update_concept(concept_id: str, payload: ConceptUpdateIn, db: Session = Depends(get_db)):
    concept = db.get(Concept, concept_id)
    if not concept:
        raise HTTPException(404, "Concept not found")
    _set_terms(db, concept, payload.name, payload.aliases)
    # Renaming or re-aliasing changes what the text matches, so rebuild the tags
    # and re-run discovery: the new wording may match content the old one missed.
    retag_everything(db)
    refresh_for_concept(db, concept.id)
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
    """Only people with an account can be routed expertise.

    Routing to an anonymous browser profile asked the admin to pick an expert
    from a list of hex codes, which is unanswerable, and the mapping would be
    destroyed the moment that person cleared their cookies.
    """
    profiles = (
        db.query(Profile)
        .filter(Profile.account_id.isnot(None))
        .order_by(Profile.display_name)
        .all()
    )
    return [{"id": p.id, "label": p.label, "verified": True} for p in profiles]


@router.get("/endorsements", dependencies=[Depends(require_admin)])
def endorsement_ranking(db: Session = Depends(get_db)):
    """Who the team actually treats as an expert, and on what.

    Anyone can endorse, so this is the evidence an admin maps expertise from.
    """
    rows = (
        db.query(ImpactEvent.beneficiary_profile_id, func.count())
        .filter(ImpactEvent.event_type == "sme_endorsed")
        .group_by(ImpactEvent.beneficiary_profile_id)
        .all()
    )
    counts = dict(rows)
    profiles = {
        p.id: p for p in db.query(Profile).filter(Profile.id.in_(list(counts) or [""])).all()
    }
    # A concept's name is a derived property, not a column, so the topics are
    # resolved from concept ids rather than selected in the join.
    topic_rows = (
        db.query(ImpactEvent.beneficiary_profile_id, ItemConcept.concept_id)
        .join(ItemConcept, ItemConcept.item_id == ImpactEvent.item_id)
        .filter(ImpactEvent.event_type == "sme_endorsed")
        .distinct()
        .all()
    )
    names = {
        c.id: c.name
        for c in db.query(Concept)
        .filter(Concept.id.in_([cid for _, cid in topic_rows] or [""]))
        .all()
    }
    concepts_by_profile: dict[str, set[str]] = {}
    for pid, cid in topic_rows:
        if cid in names:
            concepts_by_profile.setdefault(pid, set()).add(names[cid])

    return sorted(
        (
            {
                "profile_id": pid,
                "label": profiles[pid].label if pid in profiles else "Unknown",
                "has_account": pid in profiles and profiles[pid].has_account,
                "endorsements": count,
                "topics": sorted(concepts_by_profile.get(pid, ())),
            }
            for pid, count in counts.items()
        ),
        key=lambda e: (-e["endorsements"], e["label"]),
    )


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
