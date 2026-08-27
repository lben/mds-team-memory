from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..knowledge import item_dict
from ..models import KnowledgeItem, Profile

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("")
def feed(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    """Latest team knowledge, newest first, corroboration groups collapsed to
    their newest member."""
    items = (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.visibility == "team",
            KnowledgeItem.kind.in_(("note", "excerpt", "answer")),
        )
        .order_by(KnowledgeItem.created_at.desc())
        .limit(120)
        .all()
    )
    seen: set[str] = set()
    result = []
    for item in items:
        key = item.group_id or item.id
        if key in seen:
            continue
        seen.add(key)
        result.append(item_dict(db, item, profile))
        if len(result) >= 30:
            break
    return result
