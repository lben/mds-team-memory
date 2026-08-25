from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..models import Notification, Profile

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    ids: list[str] | None = None  # None marks everything read


@router.get("")
def list_notifications(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    rows = (
        db.query(Notification)
        .filter(Notification.profile_id == profile.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "unread": sum(1 for n in rows if not n.read),
        "notifications": [
            {
                "id": n.id,
                "kind": n.kind,
                "message": n.message,
                "item_id": n.item_id,
                "read": n.read,
                "created_at": n.created_at.isoformat() + "Z",
            }
            for n in rows
        ],
    }


@router.post("/read")
def mark_read(
    payload: ReadIn, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    query = db.query(Notification).filter(Notification.profile_id == profile.id)
    if payload.ids:
        query = query.filter(Notification.id.in_(payload.ids))
    query.update({"read": True})
    db.commit()
    return {"ok": True}
