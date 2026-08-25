from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import get_profile
from ..db import get_db
from ..models import Profile
from ..searchsvc import search_all

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
def search(
    q: str = Query(min_length=1, max_length=500),
    profile: Profile = Depends(get_profile),
    db: Session = Depends(get_db),
):
    return search_all(db, profile, q)
