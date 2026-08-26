"""Concept-to-concept link discovery, evidence and review state.

Links are stored rather than recomputed per request so that an admin's approve
or reject decision survives, and so a rejected link keeps accumulating evidence
and can be reinstated later.
"""

from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .models import (
    RELATED_TO_ID,
    Concept,
    Document,
    DocumentPassage,
    ItemConcept,
    KnowledgeItem,
    Relationship,
    RelationshipType,
    utcnow,
)

VISIBLE_STATES = ("suggested", "confirmed")


def _team_subject_ids(db: Session, concept_id: str) -> set[tuple[str, str]]:
    """(kind, id) of team-visible subjects tagged with a concept.

    Private items are excluded here, which is what keeps private scratchpad
    content out of link counts, evidence and the map.
    """
    item_rows = db.execute(
        select(ItemConcept.subject_id)
        .join(
            KnowledgeItem,
            (ItemConcept.subject_kind == "item") & (KnowledgeItem.id == ItemConcept.subject_id),
        )
        .where(ItemConcept.concept_id == concept_id, KnowledgeItem.visibility == "team")
    ).all()
    passage_rows = db.execute(
        select(ItemConcept.subject_id).where(
            ItemConcept.concept_id == concept_id, ItemConcept.subject_kind == "passage"
        )
    ).all()
    return {("item", r[0]) for r in item_rows} | {("passage", r[0]) for r in passage_rows}


def shared_subjects(db: Session, concept_a: str, concept_b: str) -> list[tuple[str, str]]:
    return sorted(_team_subject_ids(db, concept_a) & _team_subject_ids(db, concept_b))


def find_link(db: Session, concept_a: str, concept_b: str) -> Relationship | None:
    """A concept pair has at most one link, in either stored direction."""
    return (
        db.query(Relationship)
        .filter(
            Relationship.src_kind == "concept",
            Relationship.dst_kind == "concept",
            (
                ((Relationship.src_id == concept_a) & (Relationship.dst_id == concept_b))
                | ((Relationship.src_id == concept_b) & (Relationship.dst_id == concept_a))
            ),
        )
        .first()
    )


def refresh_for_item(db: Session, concept_ids: list[str]) -> None:
    """Update counts for every concept pair touched by a new contribution.

    Creates a `suggested` link once a pair reaches the configured threshold, and
    keeps refreshing the count of links that already exist — including rejected
    ones, so an admin can watch the evidence for a rejection grow.
    """
    if len(concept_ids) < 2:
        return
    for a, b in combinations(sorted(set(concept_ids)), 2):
        count = len(shared_subjects(db, a, b))
        link = find_link(db, a, b)
        if link:
            link.occurrence_count = count
            if link.state == "suggested":
                link.evidence = _auto_evidence(count)
        elif count >= config.COOCCURRENCE_MIN:
            db.add(
                Relationship(
                    src_kind="concept",
                    src_id=a,
                    dst_kind="concept",
                    dst_id=b,
                    relationship_type_id=RELATED_TO_ID,
                    state="suggested",
                    occurrence_count=count,
                    evidence=_auto_evidence(count),
                )
            )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _auto_evidence(count: int) -> str:
    return f"Mentioned together in {count} team {'entry' if count == 1 else 'entries'}."


def recount(db: Session, link: Relationship) -> int:
    link.occurrence_count = len(shared_subjects(db, link.src_id, link.dst_id))
    return link.occurrence_count


def link_dict(db: Session, link: Relationship) -> dict:
    src = db.get(Concept, link.src_id)
    dst = db.get(Concept, link.dst_id)
    return {
        "id": link.id,
        "src_id": link.src_id,
        "src_name": src.name if src else "(deleted concept)",
        "dst_id": link.dst_id,
        "dst_name": dst.name if dst else "(deleted concept)",
        "type_id": link.relationship_type_id,
        "type_name": link.relationship_type.name,
        "state": link.state,
        "occurrence_count": link.occurrence_count,
        "evidence": link.evidence,
        "reviewed_by": link.reviewed_by,
        "reviewed_at": link.reviewed_at.isoformat() + "Z" if link.reviewed_at else None,
        "review_note": link.review_note,
        "created_at": link.created_at.isoformat() + "Z",
    }


def evidence_detail(db: Session, link: Relationship) -> dict:
    """The actual team-visible contributions behind a link, for drill-down."""
    subjects = shared_subjects(db, link.src_id, link.dst_id)
    items, passages = [], []
    for kind, subject_id in subjects:
        if kind == "item":
            item = db.get(KnowledgeItem, subject_id)
            if item and item.visibility == "team":
                items.append(
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "body": item.body,
                        "parent_id": item.parent_id,
                        "created_at": item.created_at.isoformat() + "Z",
                    }
                )
        else:
            passage = db.get(DocumentPassage, subject_id)
            if passage:
                document = db.get(Document, passage.document_id)
                passages.append(
                    {
                        "id": passage.id,
                        "document_id": passage.document_id,
                        "filename": document.filename if document else "",
                        "locator": passage.locator,
                        "text": passage.text,
                    }
                )
    src = db.get(Concept, link.src_id)
    dst = db.get(Concept, link.dst_id)
    return {
        "link_id": link.id,
        "src_name": src.name if src else "",
        "dst_name": dst.name if dst else "",
        "occurrence_count": len(subjects),
        "items": items,
        "passages": passages,
    }


def set_state(db: Session, link: Relationship, state: str, admin_username: str, note: str | None) -> None:
    link.state = state
    link.reviewed_by = admin_username
    link.reviewed_at = utcnow()
    link.review_note = (note or "").strip() or None
    recount(db, link)
    db.commit()


def type_usage(db: Session, type_id: str) -> int:
    return (
        db.query(func.count(Relationship.id))
        .filter(Relationship.relationship_type_id == type_id)
        .scalar()
        or 0
    )


def ensure_builtin_types(db: Session) -> None:
    """Seed the protected vocabulary on databases that predate it."""
    from .models import BUILTIN_TYPES

    for type_id, name in BUILTIN_TYPES.items():
        if not db.get(RelationshipType, type_id):
            db.add(RelationshipType(id=type_id, name=name, is_builtin=True))
    db.commit()
