from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..impact import profile_totals
from ..models import Profile

router = APIRouter(prefix="/api/profile", tags=["profile"])


class DisplayNameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


@router.get("")
def me(profile: Profile = Depends(get_profile), db: Session = Depends(get_db)):
    return {
        "id": profile.id,
        "label": profile.label,
        "display_name": profile.display_name,
        # No longer a hardcoded lie: it is true exactly when this profile
        # belongs to an account, which is also what protects it from a
        # cookie clear.
        "verified": profile.has_account,
        "totals": profile_totals(db, profile.id),
    }


@router.put("")
def set_display_name(
    payload: DisplayNameIn,
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    profile.display_name = payload.display_name.strip()
    db.commit()
    return {"label": profile.label, "verified": profile.has_account}
