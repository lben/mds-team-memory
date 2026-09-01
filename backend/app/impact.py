from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .live import queue_wake
from .models import ImpactEvent, KnowledgeItem, Notification, Profile

POINTS = {
    "helped": 1,
    "group_helped": 1,
    "answer_accepted": 3,
    "correction_adopted": 2,
    "sme_endorsed": 2,
}


def record_event(
    db: Session,
    event_type: str,
    beneficiary_profile_id: str,
    dedup_key: str,
    actor_profile_id: str | None = None,
    item_id: str | None = None,
) -> bool:
    """Insert one immutable impact event. Returns False if it already exists."""
    try:
        with db.begin_nested():
            db.add(
                ImpactEvent(
                    event_type=event_type,
                    actor_profile_id=actor_profile_id,
                    beneficiary_profile_id=beneficiary_profile_id,
                    item_id=item_id,
                    points=POINTS[event_type],
                    dedup_key=dedup_key,
                )
            )
        return True
    except IntegrityError:
        return False


def notify(
    db: Session,
    profile_id: str,
    kind: str,
    message: str,
    item_id: str | None = None,
    dedup_key: str | None = None,
) -> None:
    try:
        with db.begin_nested():
            db.add(
                Notification(
                    profile_id=profile_id,
                    kind=kind,
                    message=message,
                    item_id=item_id,
                    dedup_key=dedup_key,
                )
            )
        queue_wake(db, profile_id)
    except IntegrityError:
        pass  # duplicate notification; nothing new to wake anyone for


def _helped_keys(db: Session, item: KnowledgeItem, actor_id: str, beneficiary_id: str) -> list[str]:
    """Every dedup key that can represent an existing 'helped' mark by this actor
    for this beneficiary. Per-item keys of group members are included so a mark
    placed before the item joined a corroboration group still counts."""
    if not item.group_id:
        return [f"helped:{actor_id}:item:{item.id}"]
    keys = [f"helped:{actor_id}:group:{item.group_id}:{beneficiary_id}"]
    member_ids = (
        db.query(KnowledgeItem.id)
        .filter(
            KnowledgeItem.group_id == item.group_id,
            KnowledgeItem.author_profile_id == beneficiary_id,
        )
        .all()
    )
    keys += [f"helped:{actor_id}:item:{mid}" for (mid,) in member_ids]
    return keys


def already_helped(db: Session, item: KnowledgeItem, actor_id: str) -> bool:
    keys = _helped_keys(db, item, actor_id, item.author_profile_id)
    return db.query(ImpactEvent).filter(ImpactEvent.dedup_key.in_(keys)).first() is not None


def mark_helped(db: Session, item: KnowledgeItem, actor: Profile) -> tuple[bool, str | None]:
    """'Helped me' on an item. Idempotent per actor — including across group
    formation; grouped duplicates by the same author pay once; other
    contributors in the group each earn +1 once."""
    if item.author_profile_id == actor.id:
        return False, "You cannot mark your own contribution"  # a refusal
    if already_helped(db, item, actor.id):
        created = False
    else:
        key = _helped_keys(db, item, actor.id, item.author_profile_id)[0]
        created = record_event(db, "helped", item.author_profile_id, key, actor.id, item.id)
    if created:
        notify(
            db,
            item.author_profile_id,
            "helped",
            "A teammate marked your contribution as helpful.",
            item.id,
        )
    if item.group_id:
        others = (
            db.query(KnowledgeItem.author_profile_id)
            .filter(
                KnowledgeItem.group_id == item.group_id,
                KnowledgeItem.author_profile_id.notin_([item.author_profile_id, actor.id]),
            )
            .distinct()
            .all()
        )
        for (other_id,) in others:
            existing = (
                db.query(ImpactEvent)
                .filter(ImpactEvent.dedup_key.in_(_helped_keys(db, item, actor.id, other_id)))
                .first()
            )
            # _helped_keys owns this format; writing it a second time here is
            # how idempotency breaks with no symptom at the call site.
            if existing is None and record_event(
                db,
                "group_helped",
                other_id,
                _helped_keys(db, item, actor.id, other_id)[0],
                actor.id,
                item.id,
            ):
                notify(
                    db,
                    other_id,
                    "helped",
                    "Corroborated knowledge you contributed helped a teammate.",
                    item.id,
                )
    db.commit()
    # (created, refusal). Already-marked is not a refusal — it is the same
    # answer as marking it, so the caller must not turn it into an error. The
    # router used to tell them apart by comparing this sentence's wording.
    return created, None


# Contributions that add knowledge. Asking a question is not sharing.
SHARED_KINDS = ("note", "excerpt", "answer", "correction")


def shared_counts(db: Session, profile_ids: list[str] | None = None, since=None) -> dict[str, int]:
    """How many distinct pieces of knowledge each profile has shared.

    This is a visible encouragement counter, never points: reposting the same
    normalized content counts once, and it never feeds the impact score.
    """
    query = db.query(
        KnowledgeItem.author_profile_id,
        func.count(func.distinct(func.coalesce(KnowledgeItem.normalized_hash, KnowledgeItem.id))),
    ).filter(
        KnowledgeItem.visibility == "team",
        KnowledgeItem.kind.in_(SHARED_KINDS),
    )
    if profile_ids is not None:
        query = query.filter(KnowledgeItem.author_profile_id.in_(profile_ids or [""]))
    if since is not None:
        query = query.filter(KnowledgeItem.created_at >= since)
    return dict(query.group_by(KnowledgeItem.author_profile_id).all())


def shared_count(db: Session, profile_id: str) -> int:
    return shared_counts(db, [profile_id]).get(profile_id, 0)


def profile_totals(db: Session, profile_id: str) -> dict:
    rows = (
        db.query(ImpactEvent.event_type, func.count(), func.sum(ImpactEvent.points))
        .filter(ImpactEvent.beneficiary_profile_id == profile_id)
        .group_by(ImpactEvent.event_type)
        .all()
    )
    by_type = {t: c for t, c, _ in rows}
    return {
        "shared": shared_count(db, profile_id),
        "helped": by_type.get("helped", 0) + by_type.get("group_helped", 0),
        "accepted": by_type.get("answer_accepted", 0),
        "corrections": by_type.get("correction_adopted", 0),
        "endorsements": by_type.get("sme_endorsed", 0),
        "score": sum(s or 0 for _, _, s in rows),
    }
