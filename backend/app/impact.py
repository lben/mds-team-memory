from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
    except IntegrityError:
        pass


def mark_helped(db: Session, item: KnowledgeItem, actor: Profile) -> tuple[bool, str | None]:
    """'Helped me' on an item. Idempotent per actor; grouped duplicates by the
    same author pay once; other contributors in the group each earn +1 once."""
    if item.author_profile_id == actor.id:
        return False, "You cannot mark your own contribution"
    if item.group_id:
        key = f"helped:{actor.id}:group:{item.group_id}:{item.author_profile_id}"
    else:
        key = f"helped:{actor.id}:item:{item.id}"
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
            if record_event(
                db,
                "group_helped",
                other_id,
                f"helped:{actor.id}:group:{item.group_id}:{other_id}",
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
    return created, None if created else "Already marked"


def profile_totals(db: Session, profile_id: str) -> dict:
    rows = (
        db.query(ImpactEvent.event_type, func.count(), func.sum(ImpactEvent.points))
        .filter(ImpactEvent.beneficiary_profile_id == profile_id)
        .group_by(ImpactEvent.event_type)
        .all()
    )
    by_type = {t: c for t, c, _ in rows}
    return {
        "helped": by_type.get("helped", 0) + by_type.get("group_helped", 0),
        "accepted": by_type.get("answer_accepted", 0),
        "corrections": by_type.get("correction_adopted", 0),
        "endorsements": by_type.get("sme_endorsed", 0),
        "score": sum(s or 0 for _, _, s in rows),
    }
