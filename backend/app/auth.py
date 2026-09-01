import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import Account, Profile, utcnow
from .models import Session as LoginSession

PROFILE_COOKIE = "mds_profile"
SESSION_COOKIE = "mds_session"
PBKDF2_ITERATIONS = 200_000


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie_kwargs() -> dict:
    return {"httponly": True, "samesite": "lax", "secure": config.SECURE_COOKIES, "path": "/"}


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


# --- who is signed in -------------------------------------------------------


def get_account(request: Request, db: Session = Depends(get_db)) -> Account | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = db.get(LoginSession, hash_token(token))
    if not session or session.expires_at < utcnow():
        return None
    return db.get(Account, session.account_id)


def get_admin(account: Account | None = Depends(get_account)) -> Account | None:
    return account if account and account.is_admin else None


def require_admin(admin: Account | None = Depends(get_admin)) -> Account:
    if not admin:
        raise HTTPException(status_code=401, detail="Admin login required")
    return admin


# --- who you are ------------------------------------------------------------


def _browser_profile(request: Request, response: Response, db: Session) -> Profile:
    """The profile this browser's cookie identifies, creating one if needed."""
    token = request.cookies.get(PROFILE_COOKIE)
    if token:
        profile = db.query(Profile).filter_by(token_hash=hash_token(token)).first()
        if profile:
            return profile
    token = secrets.token_hex(32)
    profile = Profile(token_hash=hash_token(token))
    db.add(profile)
    db.commit()
    response.set_cookie(PROFILE_COOKIE, token, max_age=10 * 365 * 24 * 3600, **_cookie_kwargs())
    return profile


def account_profile(db: Session, account: Account) -> Profile:
    """The profile an account owns, creating one if it has none. It has no
    token_hash: an account is not a browser."""
    profile = db.query(Profile).filter_by(account_id=account.id).first()
    if profile is None:
        profile = Profile(account_id=account.id, display_name=account.username, claim_locked=True)
        db.add(profile)
        db.commit()
    return profile


def profile_from_cookies(db: Session, cookies) -> Profile | None:
    """Who a connection belongs to, without creating anything.

    The same order `get_profile` uses — session first, browser cookie second —
    for callers that have cookies but no Request/Response to hand, such as the
    notification websocket. Resolving it any other way lets the socket and the
    REST API disagree about who you are.
    """
    token = cookies.get(SESSION_COOKIE)
    if token:
        session = db.get(LoginSession, hash_token(token))
        if session and session.expires_at >= utcnow():
            account = db.get(Account, session.account_id)
            if account:
                return account_profile(db, account)
    token = cookies.get(PROFILE_COOKIE)
    if token:
        return db.query(Profile).filter_by(token_hash=hash_token(token)).first()
    return None


def get_profile(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> Profile:
    """Who the caller is. Signing in changes this — that is the whole point:
    an admin's own contributions used to be credited to a hex code because
    identity ignored the session entirely."""
    account = get_account(request, db)
    if account:
        return account_profile(db, account)
    return _browser_profile(request, response, db)


# --- signing in and out -----------------------------------------------------


def sign_in(db: Session, request: Request, response: Response, account: Account) -> Profile:
    """Start a session and, if this is the first sign-in on this browser, give
    the account the anonymous work already done here.

    The claim is a one-shot per browser: it is what makes "use it first, make an
    account later" keep your data, and locking it afterwards is what stops a
    second person on the same machine absorbing a colleague's contributions.
    """
    browser = _browser_profile(request, response, db)
    profile = db.query(Profile).filter_by(account_id=account.id).first()

    if profile is None and browser.account_id is None and not browser.claim_locked:
        browser.account_id = account.id
        if not browser.display_name:
            browser.display_name = account.username
        # The profile now belongs to the account, not to this browser. Leaving
        # the cookie pointing at it would mean signing out did not make you
        # anonymous again — you would carry on posting as the account you just
        # left. The session is what resolves this profile from here on.
        browser.token_hash = None
        profile = browser
    browser.claim_locked = True
    db.commit()

    if profile is None:
        profile = account_profile(db, account)

    token = secrets.token_hex(32)
    db.add(
        LoginSession(
            token_hash=hash_token(token),
            account_id=account.id,
            expires_at=utcnow() + timedelta(hours=config.SESSION_HOURS),
        )
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=config.SESSION_HOURS * 3600, **_cookie_kwargs()
    )
    return profile


def sign_out(request: Request, response: Response, db: Session) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = db.get(LoginSession, hash_token(token))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
