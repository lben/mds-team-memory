from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..knowledge import item_dict, process_after_save
from ..models import KnowledgeItem, Profile, Scratchpad, utcnow
from ..text import find_matches

router = APIRouter(prefix="/api/scratchpad", tags=["scratchpad"])


class ContentIn(BaseModel):
    content: str = Field(max_length=2_000_000)


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ShareIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


def _pad_dict(pad: Scratchpad) -> dict:
    return {
        "id": pad.id,
        "name": pad.name or "My scratchpad",
        "is_default": pad.is_default,
        "content": pad.content,
        "updated_at": pad.updated_at.isoformat() + "Z",
    }


def _own_pad(db: Session, pad_id: str, profile: Profile) -> Scratchpad:
    pad = db.get(Scratchpad, pad_id)
    if not pad or pad.profile_id != profile.id:
        raise HTTPException(404, "Scratchpad not found")
    return pad


@router.get("")
def my_scratchpads(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    pads = (
        db.query(Scratchpad)
        .filter(Scratchpad.profile_id == profile.id)
        .order_by(Scratchpad.is_default.desc(), Scratchpad.updated_at.desc())
        .all()
    )
    if not pads:
        pad = Scratchpad(profile_id=profile.id, is_default=True, content="")
        db.add(pad)
        db.commit()
        pads = [pad]
    return {"default": _pad_dict(pads[0]), "others": [_pad_dict(p) for p in pads[1:]]}


@router.post("")
def create_scratchpad(
    payload: CreateIn, profile: Profile = Depends(get_profile), db: Session = Depends(get_db)
):
    pad = Scratchpad(profile_id=profile.id, is_default=False, name=payload.name, content="")
    db.add(pad)
    db.commit()
    return _pad_dict(pad)


@router.put("/{pad_id}")
def save_scratchpad(
    pad_id: str,
    payload: ContentIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    pad = _own_pad(db, pad_id, profile)
    pad.content = payload.content
    pad.updated_at = utcnow()
    db.commit()
    return {"saved": True, "updated_at": pad.updated_at.isoformat() + "Z"}


@router.get("/{pad_id}/find")
def find_in_scratchpad(
    pad_id: str,
    q: str = Query(min_length=1, max_length=300),
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    pad = _own_pad(db, pad_id, profile)
    return {"matches": find_matches(pad.content, q)}


@router.post("/{pad_id}/share")
def share_selection(
    pad_id: str,
    payload: ShareIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    pad = _own_pad(db, pad_id, profile)
    item = KnowledgeItem(
        kind="excerpt",
        body=payload.text.strip(),
        visibility="team",
        author_profile_id=profile.id,
        source_item_id=f"scratchpad:{pad.id}",
    )
    db.add(item)
    db.commit()
    corroboration = process_after_save(db, item)
    return {"item": item_dict(db, item, profile), "corroboration": corroboration}
