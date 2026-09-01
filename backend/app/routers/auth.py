from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_account, hash_password, sign_in, sign_out, verify_password
from ..db import get_db
from ..models import Account

router = APIRouter(prefix="/api/auth", tags=["auth"])


class CredentialsIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=200)


@router.get("/state")
def state(account: Account | None = Depends(get_account)):
    return {
        "signed_in": account is not None,
        "username": account.username if account else None,
        "is_admin": bool(account and account.is_admin),
    }


@router.post("/signup")
def signup(
    payload: CredentialsIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Anyone can make a contributor account. Admin accounts are still made only
    with `manage.py create-admin`, so signing up never grants admin rights."""
    username = payload.username.strip()
    account = Account(username=username, password_hash=hash_password(payload.password))
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "That username is taken")
    profile = sign_in(db, request, response, account)
    return {"username": account.username, "is_admin": False, "label": profile.label}


@router.post("/login")
def login(
    payload: CredentialsIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    account = db.query(Account).filter_by(username=payload.username.strip()).first()
    if not account or not verify_password(payload.password, account.password_hash):
        raise HTTPException(401, "Invalid username or password")
    profile = sign_in(db, request, response, account)
    return {"username": account.username, "is_admin": account.is_admin, "label": profile.label}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    sign_out(request, response, db)
    return {"ok": True}
