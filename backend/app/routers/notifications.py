import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import PROFILE_COOKIE, get_profile, hash_token
from ..db import SessionLocal, get_db
from ..live import hub
from ..models import Notification, Profile

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
ws_router = APIRouter()


@ws_router.websocket("/ws/notifications")
async def notifications_socket(websocket: WebSocket):
    """Push a wake-up when this profile gets a notification.

    Identified by the same HttpOnly profile cookie as the REST API, so a
    browser can only ever subscribe to its own notifications.
    """
    token = websocket.cookies.get(PROFILE_COOKIE)
    profile_id = None
    if token:
        db = SessionLocal()
        try:
            profile = db.query(Profile).filter_by(token_hash=hash_token(token)).first()
            profile_id = profile.id if profile else None
        finally:
            db.close()
    if not profile_id:
        # 1008 = policy violation; the browser falls back to polling.
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    hub.register(profile_id, queue)
    # Watch the socket as well as the queue: waiting only on the queue would
    # never observe a disconnect, so the connection would keep the server alive
    # and a restart would hang until it was killed.
    receiver = asyncio.ensure_future(websocket.receive())
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            waiter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {receiver, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in done:
                waiter.cancel()
                break
            await websocket.send_json(waiter.result())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()
        hub.unregister(profile_id, queue)


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
