"""Adding team knowledge and keeping a private scratchpad need an account; reading and searching do not."""

import io
import uuid


def test_every_way_to_contribute_asks_for_an_account(make_client):
    member, visitor = make_client(), make_client(account=False)
    question = member.post("/api/questions", json={"body": f"Who owns the {uuid.uuid4().hex[:6]} feed?"}).json()["id"]
    note = member.post("/api/capture", data={"body": "The feed lands at six."}).json()["item"]["id"]
    document = member.post("/api/documents", files={"file": ("runbook.txt", io.BytesIO(b"Step one.\n\nStep two."),
                                                              "text/plain")}).json()
    passage = member.get(f"/api/documents/{document['id']}").json()["passages"][0]["id"]
    pad = member.get("/api/scratchpad").json()["default"]["id"]
    member.put(f"/api/scratchpad/{pad}", json={"content": "Private member note about the feed."})

    attempts = [
        visitor.post("/api/capture", data={"body": "An anonymous note."}),
        visitor.post("/api/capture", files={"file": ("notes.txt", io.BytesIO(b"Anonymous upload."), "text/plain")}),
        visitor.post("/api/documents", files={"file": ("notes.txt", io.BytesIO(b"Anonymous upload."), "text/plain")}),
        visitor.post("/api/questions", json={"body": "An anonymous question?"}),
        visitor.post(f"/api/questions/{question}/answers", json={"body": "An anonymous answer."}),
        visitor.post(f"/api/items/{note}/corrections", json={"body": "An anonymous correction."}),
        visitor.post(f"/api/passages/{passage}/share", json={}),
        visitor.post(f"/api/scratchpad/{pad}/share", json={"text": "An anonymous share."}),
        visitor.get("/api/scratchpad"),
        visitor.post("/api/scratchpad", json={"name": "Anonymous notes"}),
        visitor.put(f"/api/scratchpad/{pad}", json={"content": "Overwritten anonymously."}),
        visitor.get(f"/api/scratchpad/{pad}/find", params={"q": "feed"}),
    ]
    assert [response.status_code for response in attempts] == [401] * len(attempts)
    assert all("create an account" in response.json()["detail"] for response in attempts)

    # Everything stays readable without one.
    assert any(item["id"] == note for item in visitor.get("/api/feed").json())
    assert visitor.get(f"/api/questions/{question}").status_code == 200
    found = visitor.get("/api/search", params={"q": "feed"})
    assert found.status_code == 200 and found.json()["scratchpad"] == []
    assert member.get("/api/search", params={"q": "feed"}).json()["scratchpad"]  # the owner still finds their notes
    assert visitor.get("/api/graph/global").status_code == 200
    assert visitor.get(f"/api/documents/{document['id']}").status_code == 200


def test_a_scratchpad_from_before_accounts_were_required_is_hidden_until_its_browser_signs_in(make_client):
    from app.db import SessionLocal
    from app.models import Scratchpad

    visitor = make_client(account=False)
    note = f"kept-{uuid.uuid4().hex[:8]} before accounts were required"
    profile = visitor.get("/api/profile").json()["id"]
    with SessionLocal() as db:  # an anonymous scratchpad written before this rule
        db.add(Scratchpad(profile_id=profile, is_default=True, content=note))
        db.commit()

    assert visitor.get("/api/scratchpad").status_code == 401
    assert visitor.get("/api/search", params={"q": note.split()[0]}).json()["scratchpad"] == []

    # The browser's first sign-in claims it, as it always has.
    assert visitor.post("/api/auth/signup", json={"username": f"keeper-{uuid.uuid4().hex[:6]}",
                                                  "password": "a-good-password"}).status_code == 200
    assert visitor.get("/api/scratchpad").json()["default"]["content"] == note
    assert visitor.get("/api/search", params={"q": note.split()[0]}).json()["scratchpad"]
