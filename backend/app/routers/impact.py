from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..impact import profile_totals, shared_counts
from ..models import ImpactEvent, Profile, utcnow

router = APIRouter(prefix="/api/impact", tags=["impact"])


@router.get("")
def impact(
    period: str = Query("30d", pattern="^(30d|all)$"),
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    query = db.query(
        ImpactEvent.beneficiary_profile_id,
        ImpactEvent.event_type,
        func.count(),
        func.sum(ImpactEvent.points),
    )
    if period == "30d":
        query = query.filter(ImpactEvent.created_at >= utcnow() - timedelta(days=30))
    rows = query.group_by(ImpactEvent.beneficiary_profile_id, ImpactEvent.event_type).all()

    since = utcnow() - timedelta(days=30) if period == "30d" else None
    shared = shared_counts(db, since=since)

    def blank() -> dict:
        return {"shared": 0, "helped": 0, "accepted": 0, "corrections": 0, "endorsements": 0, "score": 0}

    by_profile: dict[str, dict] = {}
    # Contributors who have shared but not yet earned impact still appear, so
    # sharing is visible before it pays off.
    for profile_id, count in shared.items():
        by_profile.setdefault(profile_id, blank())["shared"] = count
    for profile_id, event_type, count, points in rows:
        entry = by_profile.setdefault(profile_id, blank())
        if event_type in ("helped", "group_helped"):
            entry["helped"] += count
        elif event_type == "answer_accepted":
            entry["accepted"] += count
        elif event_type == "correction_adopted":
            entry["corrections"] += count
        elif event_type == "sme_endorsed":
            entry["endorsements"] += count
        entry["score"] += points or 0

    profiles = {
        p.id: p
        for p in db.query(Profile).filter(Profile.id.in_(list(by_profile) or [""])).all()
    }
    leaderboard = sorted(
        (
            {
                "profile_id": pid,
                "label": profiles[pid].label if pid in profiles else "Unknown",
                "verified": pid in profiles and profiles[pid].has_account,
                "is_me": pid == profile.id,
                **entry,
            }
            for pid, entry in by_profile.items()
        ),
        # Impact decides the order; among contributors with equal impact the one
        # who has shared more appears first, which is a tie-break, not a ranking
        # by volume.
        key=lambda e: (-e["score"], -e["shared"], e["label"]),
    )
    # Rank reflects impact, so someone who has only shared so far is not ranked
    # last for it — their shared count is what the page shows them instead.
    # This is the only place that rule is expressed: the table renders whatever
    # comes back, so the server and the page cannot drift apart.
    for position, entry in enumerate(leaderboard, start=1):
        entry["rank"] = position if entry["score"] > 0 else None
    me = next((e for e in leaderboard if e["is_me"]), None)
    my_rank = me["rank"] if me else None
    return {
        "period": period,
        "me": {**profile_totals(db, profile.id), "rank": my_rank},
        "leaderboard": leaderboard,
    }
