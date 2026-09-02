from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ExpertiseMapping

router = APIRouter(prefix="/api/expertise", tags=["expertise"])


@router.get("")
def who_knows_what(db: Session = Depends(get_db)):
    """Who is mapped to which topic, readable by everyone.

    The whole point of routing is knowing who to ask, and that was locked behind
    the admin sign-in: an ordinary contributor wanting to know who owns prod
    access hit a login wall and had nowhere else to look. Changing a mapping
    stays an admin action — this only answers the question.

    Deliberately carries no mapping ids: nothing here can be acted on, and the
    labels are names the team already sees on every contribution.
    """
    by_profile: dict[str, dict] = {}
    for m in db.query(ExpertiseMapping).all():
        entry = by_profile.setdefault(m.profile_id, {"label": m.profile.label, "areas": []})
        entry["areas"].append(m.concept.name)
    for entry in by_profile.values():
        entry["areas"].sort()
    return sorted(by_profile.values(), key=lambda e: e["label"].lower())
