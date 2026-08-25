import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AdminSession, AdminUser, Profile, utcnow

PROFILE_COOKIE = "mds_profile"
ADMIN_COOKIE = "mds_admin"
PBKDF2_ITERATIONS = 200_000


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie_kwargs() -> dict:
    return {"httponly": True, "samesite": "lax", "secure": config.SECURE_COOKIES, "path": "/"}


def get_profile(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> Profile:
    """Return the current browser profile, creating one (and its cookie) if needed."""
    token = request.cookies.get(PROFILE_COOKIE)
    if token:
        profile = db.query(Profile).filter_by(token_hash=_hash_token(token)).first()
        if profile:
            return profile
    token = secrets.token_hex(32)
    profile = Profile(token_hash=_hash_token(token))
    db.add(profile)
    db.commit()
    response.set_cookie(PROFILE_COOKIE, token, max_age=10 * 365 * 24 * 3600, **_cookie_kwargs())
    return profile


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        _scheme, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except ValueError:
        return False


def create_admin_session(db: Session, response: Response, admin: AdminUser) -> None:
    token = secrets.token_hex(32)
    db.add(
        AdminSession(
            token_hash=_hash_token(token),
            admin_user_id=admin.id,
            expires_at=utcnow() + timedelta(hours=config.ADMIN_SESSION_HOURS),
        )
    )
    db.commit()
    response.set_cookie(ADMIN_COOKIE, token, max_age=config.ADMIN_SESSION_HOURS * 3600, **_cookie_kwargs())


def get_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser | None:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return None
    session = db.get(AdminSession, _hash_token(token))
    if not session or session.expires_at < utcnow():
        return None
    return db.get(AdminUser, session.admin_user_id)


def require_admin(admin: AdminUser | None = Depends(get_admin)) -> AdminUser:
    if not admin:
        raise HTTPException(status_code=401, detail="Admin login required")
    return admin


def clear_admin_session(request: Request, response: Response, db: Session) -> None:
    token = request.cookies.get(ADMIN_COOKIE)
    if token:
        session = db.get(AdminSession, _hash_token(token))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(ADMIN_COOKIE, path="/")
