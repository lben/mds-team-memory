from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..impact import profile_totals
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

    by_profile: dict[str, dict] = {}
    for profile_id, event_type, count, points in rows:
        entry = by_profile.setdefault(
            profile_id, {"helped": 0, "accepted": 0, "corrections": 0, "endorsements": 0, "score": 0}
        )
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
                "verified": False,
                "is_me": pid == profile.id,
                **entry,
            }
            for pid, entry in by_profile.items()
        ),
        key=lambda e: (-e["score"], e["label"]),
    )
    for rank, entry in enumerate(leaderboard, start=1):
        entry["rank"] = rank
    my_rank = next((e["rank"] for e in leaderboard if e["is_me"]), None)
    return {
        "period": period,
        "me": {**profile_totals(db, profile.id), "rank": my_rank},
        "leaderboard": leaderboard,
    }
