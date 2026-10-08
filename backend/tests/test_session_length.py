"""A sign-in lasts until the next Sunday 00:00 server time, so everyone signs in again by Monday."""

from datetime import datetime, timedelta, timezone
import re
import uuid


def local(utc_naive):
    return utc_naive.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def test_a_sign_in_ends_at_the_start_of_the_next_sunday(monkeypatch):
    from app import config
    from app.auth import session_end

    monkeypatch.setattr(config, "SESSION_HOURS", None)  # whatever the shell running the tests sets

    wednesday = datetime(2026, 10, 7, 10, 0)
    assert local(session_end(wednesday)) == datetime(2026, 10, 11, 0, 0)
    just_after_sunday_starts = datetime(2026, 10, 11, 0, 30)
    assert local(session_end(just_after_sunday_starts)) == datetime(2026, 10, 18, 0, 0)
    saturday_night = datetime(2026, 10, 10, 23, 0)
    assert local(session_end(saturday_night)) == datetime(2026, 10, 11, 0, 0)


def test_signing_in_stores_that_end_and_the_cookie_matches_it(make_client, monkeypatch):
    from sqlalchemy import text
    from app import config
    from app.db import SessionLocal
    from app.models import utcnow

    monkeypatch.setattr(config, "SESSION_HOURS", None)
    person = make_client(account=False)
    response = person.post("/api/auth/signup", json={"username": f"weekly{uuid.uuid4().hex[:6]}",
                                                     "password": "a-good-password"})
    assert response.status_code == 200
    with SessionLocal() as db:
        stored = db.execute(text("SELECT max(expires_at) FROM sessions")).scalar()
    ends = datetime.fromisoformat(str(stored))
    sunday = local(ends)
    assert sunday.weekday() == 6 and sunday.time() == datetime.min.time()
    assert timedelta(0) < ends - utcnow() <= timedelta(days=7)
    max_age = int(re.search(r"mds_session=[^;]+;.*?Max-Age=(\d+)", response.headers["set-cookie"], re.I).group(1))
    assert abs(max_age - (ends - utcnow()).total_seconds()) < 60

    monkeypatch.setattr(config, "SESSION_HOURS", 12)  # a deployment can still set a fixed length
    from app.auth import session_end
    assert abs((session_end() - utcnow()) - timedelta(hours=12)) < timedelta(seconds=5)
