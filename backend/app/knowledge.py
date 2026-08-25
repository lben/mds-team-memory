import uuid

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .concepts import route_question, tag_subject
from .models import ImpactEvent, KnowledgeItem, Profile, Relationship
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
    concepts = tag_subject(db, "item", item.id, (item.title or "") + " " + item.body)

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
                                rel_type="corroborates",
                                dst_kind="item",
                                dst_id=m.id,
                                evidence=f"Content is {int(similarity(item.body, m.body) * 100)}% similar after normalization.",
                                confidence="confirmed",
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


def endorsed(db: Session, item: KnowledgeItem) -> bool:
    return (
        db.query(ImpactEvent)
        .filter(ImpactEvent.event_type == "sme_endorsed", ImpactEvent.item_id == item.id)
        .first()
        is not None
    )


def item_dict(db: Session, item: KnowledgeItem, profile: Profile | None = None) -> dict:
    info = group_info(db, item)
    author = db.get(Profile, item.author_profile_id)
    marked = False
    if profile:
        if item.group_id:
            key = f"helped:{profile.id}:group:{item.group_id}:{item.author_profile_id}"
        else:
            key = f"helped:{profile.id}:item:{item.id}"
        marked = db.query(ImpactEvent).filter(ImpactEvent.dedup_key == key).first() is not None
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "body": item.body,
        "visibility": item.visibility,
        "author": author.label if author else "Unknown",
        "author_id": item.author_profile_id,
        "is_mine": bool(profile and item.author_profile_id == profile.id),
        "parent_id": item.parent_id,
        "group_id": item.group_id,
        "contributors": info["contributors"],
        "group_size": info["group_size"],
        "helped": helped_count(db, item),
        "marked_helped": marked,
        "endorsed": endorsed(db, item),
        "question_status": item.question_status,
        "accepted_answer_id": item.accepted_answer_id,
        "correction_state": item.correction_state,
        "source_document_id": item.source_document_id,
        "source_passage_id": item.source_passage_id,
        "created_at": item.created_at.isoformat() + "Z",
        "updated_at": item.updated_at.isoformat() + "Z",
    }
