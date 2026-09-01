import uuid

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .concepts import match_concepts, retag_item, route_question
from .impact import already_helped
from .models import (
    CORROBORATES_ID,
    ImpactEvent,
    KnowledgeItem,
    Notification,
    Profile,
    Relationship,
    Revision,
)
from .relationships import refresh_for_item
from .text import normalized_hash, query_terms, similarity

GROUPABLE_KINDS = ("note", "excerpt", "answer")


def _corroboration_candidates(db: Session, item: KnowledgeItem) -> list[KnowledgeItem]:
    terms = list(dict.fromkeys(query_terms(item.body)))[:15]
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    rows = db.execute(
        sql_text(
            "SELECT ki.id FROM items_fts f JOIN knowledge_items ki ON ki.rowid = f.rowid "
            "WHERE items_fts MATCH :m AND ki.visibility='team' AND ki.id != :id "
            "AND ki.kind IN ('note','excerpt','answer') ORDER BY rank LIMIT 30"
        ),
        {"m": match, "id": item.id},
    ).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return []
    return db.query(KnowledgeItem).filter(KnowledgeItem.id.in_(ids)).all()


def process_after_save(db: Session, item: KnowledgeItem) -> dict:
    """Duplicate/concept/relationship work that runs after a contribution is saved.

    Never blocks or rejects the contribution. Returns corroboration info for the UI.
    """
    item.normalized_hash = normalized_hash(item.body)
    concept_ids = retag_item(db, item)
    concepts = match_concepts(db, (item.title or "") + " " + item.body) if concept_ids else []

    corroboration = {"group_size": 1, "contributors": 1}
    if item.visibility == "team" and item.kind in GROUPABLE_KINDS:
        matches = [
            c
            for c in _corroboration_candidates(db, item)
            if c.normalized_hash == item.normalized_hash
            or similarity(item.body, c.body) >= config.SIMILARITY_THRESHOLD
        ]
        if matches:
            group_id = next((m.group_id for m in matches if m.group_id), None) or uuid.uuid4().hex
            item.group_id = group_id
            for m in matches:
                m.group_id = group_id
                try:
                    with db.begin_nested():
                        db.add(
                            Relationship(
                                src_kind="item",
                                src_id=item.id,
                                dst_kind="item",
                                dst_id=m.id,
                                relationship_type_id=CORROBORATES_ID,
                                state="confirmed",
                                evidence=f"Content is {int(similarity(item.body, m.body) * 100)}% similar after normalization.",
                            )
                        )
                except IntegrityError:
                    pass
            members = db.query(KnowledgeItem).filter(KnowledgeItem.group_id == group_id).all()
            corroboration = {
                "group_size": len(members),
                "contributors": len({m.author_profile_id for m in members}),
            }

    if item.kind == "question":
        route_question(db, item, concepts)
    db.commit()
    refresh_for_item(db, [c.id for c in concepts])
    return corroboration


def helped_count(db: Session, item: KnowledgeItem) -> int:
    q = db.query(func.count(ImpactEvent.id)).filter(ImpactEvent.event_type == "helped")
    if item.group_id:
        members = select(KnowledgeItem.id).where(KnowledgeItem.group_id == item.group_id)
        q = q.filter(ImpactEvent.item_id.in_(members))
    else:
        q = q.filter(ImpactEvent.item_id == item.id)
    return q.scalar() or 0


def group_info(db: Session, item: KnowledgeItem) -> dict:
    if not item.group_id:
        return {"group_size": 1, "contributors": 1}
    members = db.query(KnowledgeItem).filter(KnowledgeItem.group_id == item.group_id).all()
    return {
        "group_size": len(members),
        "contributors": len({m.author_profile_id for m in members}),
    }


def endorsement_count(db: Session, item: KnowledgeItem) -> int:
    return (
        db.query(ImpactEvent)
        .filter(ImpactEvent.event_type == "sme_endorsed", ImpactEvent.item_id == item.id)
        .count()
    )


def endorsed_by(db: Session, item: KnowledgeItem, profile: Profile | None) -> bool:
    if profile is None:
        return False
    return (
        db.query(ImpactEvent)
        .filter(
            ImpactEvent.event_type == "sme_endorsed",
            ImpactEvent.item_id == item.id,
            ImpactEvent.actor_profile_id == profile.id,
        )
        .first()
        is not None
    )


def item_dict(db: Session, item: KnowledgeItem, profile: Profile | None = None) -> dict:
    info = group_info(db, item)
    author = db.get(Profile, item.author_profile_id)
    marked = bool(profile) and already_helped(db, item, profile.id)
    # An answer is meaningless without the question it answers, so carry a
    # preview of it wherever the answer is shown.
    question = None
    if item.kind == "answer" and item.parent_id:
        parent = db.get(KnowledgeItem, item.parent_id)
        if parent:
            question = {"id": parent.id, "body": parent.body, "status": parent.question_status}
    return {
        "id": item.id,
        "kind": item.kind,
        "question": question,
        "title": item.title,
        "body": item.body,
        "visibility": item.visibility,
        "author": author.label if author else "Unknown",
        # Whether the name means a person or just a browser: the difference the
        # reader cares about when deciding how much to trust attribution.
        "author_verified": bool(author and author.has_account),
        "author_id": item.author_profile_id,
        "is_mine": bool(profile and item.author_profile_id == profile.id),
        "parent_id": item.parent_id,
        "group_id": item.group_id,
        "contributors": info["contributors"],
        "group_size": info["group_size"],
        "helped": helped_count(db, item),
        "marked_helped": marked,
        # Anyone may endorse, so the count is the signal an admin reads. Hiding
        # the button after the first endorsement capped every item at one.
        "endorsements": endorsement_count(db, item),
        "endorsed": endorsement_count(db, item) > 0,
        "endorsed_by_me": endorsed_by(db, item, profile),
        "question_status": item.question_status,
        "accepted_answer_id": item.accepted_answer_id,
        "correction_state": item.correction_state,
        "source_document_id": item.source_document_id,
        "source_passage_id": item.source_passage_id,
        "created_at": item.created_at.isoformat() + "Z",
        "updated_at": item.updated_at.isoformat() + "Z",
    }


def dependents_by_others(db: Session, item: KnowledgeItem) -> int:
    """Answers and corrections other people attached to this item.

    Deleting your own contribution must not destroy a teammate's, which is the
    same rule the question delete has always enforced.
    """
    return (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.parent_id == item.id,
            KnowledgeItem.author_profile_id != item.author_profile_id,
        )
        .count()
    )


def delete_item(db: Session, item: KnowledgeItem) -> None:
    """Remove a contribution and everything that pointed at it.

    One place, so a new caller cannot forget a table: the FTS index is kept by
    triggers, tags cascade, and the rest is cleaned here. Impact already earned
    by other people from this item goes with it — the item it was earned on no
    longer exists.
    """
    own_children = (
        db.query(KnowledgeItem).filter(KnowledgeItem.parent_id == item.id).all()
    )
    child_ids = [c.id for c in own_children]
    ids = [item.id] + child_ids

    db.query(Revision).filter(
        (Revision.item_id.in_(ids)) | (Revision.correction_id.in_(ids))
    ).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.item_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(ImpactEvent).filter(ImpactEvent.item_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(Relationship).filter(
        ((Relationship.src_kind == "item") & (Relationship.src_id.in_(ids)))
        | ((Relationship.dst_kind == "item") & (Relationship.dst_id.in_(ids)))
    ).delete(synchronize_session=False)

    for child in own_children:
        db.delete(child)
    db.delete(item)
    db.commit()
