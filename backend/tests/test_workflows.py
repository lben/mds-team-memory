"""API-level proof of the PRD 'meaningful automated proof' workflows 1-10."""

import io
import uuid

import pytest

from conftest import ADMIN_CREDENTIALS


def unique(text: str) -> str:
    return f"{text} {uuid.uuid4().hex[:8]}"


def test_capture_and_search(make_client):
    """W1: anonymous profile shares body-only knowledge and finds it via Search."""
    client = make_client()
    marker = uuid.uuid4().hex[:10]
    body = f"The nightly batch zeta-{marker} completes at 0745 ET."
    r = client.post("/api/capture", data={"body": body})
    assert r.status_code == 200
    assert r.json()["item"]["kind"] == "note"

    results = client.get(f"/api/search", params={"q": f"zeta-{marker}"}).json()
    assert any(marker in i["body"] for i in results["items"])
    assert "<mark>" in results["items"][0]["snippet"]


def test_natural_language_query_ranks_on_meaningful_words(make_client):
    """Regression: 'what is X' surfaced entries matching only 'is', with the
    entry actually about X last. Function words must not produce results."""
    author, reader = make_client(), make_client()
    tool = f"dbvisualizer{uuid.uuid4().hex[:6]}"
    noise = [
        "This is the nightly reconciliation checklist for the operations team.",
        "It is important that the batch is verified before it is released.",
        "There is a standing meeting where this is reviewed every week.",
    ]
    for body in noise:
        author.post("/api/capture", data={"body": body})
    target = author.post(
        "/api/capture",
        data={"body": f"{tool} is the SQL client we use to inspect the warehouse tables."},
    ).json()["item"]

    results = reader.get("/api/search", params={"q": f"what is {tool}"}).json()
    assert results["terms"] == [tool]
    # The entry about the tool is the only result; the 'is' noise never appears.
    assert [i["id"] for i in results["items"]] == [target["id"]]

    # A multi-word query ranks full coverage above partial, never the reverse.
    partial = author.post(
        "/api/capture", data={"body": "The warehouse tables are refreshed at 06:00 ET."}
    ).json()["item"]
    both = reader.get("/api/search", params={"q": f"what is {tool} warehouse tables"}).json()
    ranked = [i["id"] for i in both["items"]]
    assert ranked[0] == target["id"]
    assert partial["id"] in ranked
    assert ranked.index(target["id"]) < ranked.index(partial["id"])
    assert both["items"][0]["coverage"] > both["items"][-1]["coverage"]


def test_failed_search_becomes_question(make_client):
    """W2: the same search text becomes a team-visible question without retyping."""
    client = make_client()
    query = f"gammafeed{uuid.uuid4().hex[:8]} origin{uuid.uuid4().hex[:8]}"
    results = client.get("/api/search", params={"q": query}).json()
    assert results["items"] == []
    question = client.post("/api/questions", json={"body": results["query"]}).json()
    assert question["kind"] == "question"
    assert question["body"] == query
    assert question["question_status"] == "open"
    other = make_client()
    listed = other.get("/api/questions").json()
    assert any(q["id"] == question["id"] for q in listed)


def test_answer_accept_and_helpful(make_client):
    """W3+W4: answer, accept, mark helpful; every impact event counts once."""
    asker, answerer, reader = make_client(), make_client(), make_client()
    question = asker.post("/api/questions", json={"body": unique("How is delta reconciled")}).json()
    answer = answerer.post(
        f"/api/questions/{question['id']}/answers", json={"body": unique("Via the overnight job")}
    ).json()

    listed = asker.get(f"/api/questions/{question['id']}").json()
    assert listed["question_status"] == "answered"

    # Only the asker can accept.
    assert (
        reader.post(f"/api/questions/{question['id']}/accept", json={"answer_id": answer["id"]}).status_code
        == 403
    )
    r = asker.post(f"/api/questions/{question['id']}/accept", json={"answer_id": answer["id"]})
    assert r.json()["impact_created"] is True
    # Accepting again is idempotent.
    r = asker.post(f"/api/questions/{question['id']}/accept", json={"answer_id": answer["id"]})
    assert r.json()["impact_created"] is False

    # Helped me once per profile.
    assert reader.post(f"/api/items/{answer['id']}/helped").json()["created"] is True
    assert reader.post(f"/api/items/{answer['id']}/helped").json()["created"] is False
    # Authors cannot help themselves.
    assert answerer.post(f"/api/items/{answer['id']}/helped").status_code == 400

    totals = answerer.get("/api/profile").json()["totals"]
    assert totals["accepted"] == 1
    assert totals["helped"] == 1
    assert totals["score"] == 4  # 3 accepted + 1 helped

    detail = reader.get(f"/api/questions/{question['id']}").json()
    assert detail["question_status"] == "resolved"
    assert detail["accepted_answer_id"] == answer["id"]
    # Resolved questions can still receive answers.
    late = reader.post(
        f"/api/questions/{question['id']}/answers", json={"body": unique("Late detail")}
    )
    assert late.status_code == 200


def test_shared_counter_encourages_before_impact(make_client):
    """Sharing is counted and visible to everyone even with zero impact, but it
    never earns points (PRD 11: share an item = 0) or changes rank."""
    author, reader = make_client(), make_client()
    assert author.get("/api/profile").json()["totals"]["shared"] == 0

    fact = f"The eta-{uuid.uuid4().hex[:8]} export runs after the close."
    first = author.post("/api/capture", data={"body": fact}).json()
    assert first["shared_total"] == 1
    author.post("/api/capture", data={"body": f"Unrelated note {uuid.uuid4().hex}"})

    totals = author.get("/api/profile").json()["totals"]
    assert totals["shared"] == 2
    assert totals["score"] == 0  # sharing alone is worth no points

    # Reposting the same content does not inflate the counter.
    repeat = author.post("/api/capture", data={"body": fact}).json()
    assert repeat["shared_total"] == 2

    # Asking a question is not sharing knowledge.
    author.post("/api/questions", json={"body": f"Where is the eta export documented? {uuid.uuid4().hex}"})
    assert author.get("/api/profile").json()["totals"]["shared"] == 2

    # Others can see it, with no impact yet and therefore no rank.
    board = reader.get("/api/impact", params={"period": "all"}).json()
    me = next(e for e in board["leaderboard"] if e["profile_id"] == first["item"]["author_id"])
    assert me["shared"] == 2
    assert me["score"] == 0
    author_view = author.get("/api/impact", params={"period": "all"}).json()
    assert author_view["me"]["shared"] == 2
    assert author_view["me"]["rank"] is None

    # Sharing a scratchpad excerpt counts too.
    pad = author.get("/api/scratchpad").json()["default"]
    author.put(f"/api/scratchpad/{pad['id']}", json={"content": "A useful private line to share."})
    shared = author.post(
        f"/api/scratchpad/{pad['id']}/share", json={"text": "A useful private line to share."}
    ).json()
    assert shared["shared_total"] == 3


def test_notifications_are_pushed_not_polled(make_client):
    """The server wakes a profile's open tab when it gets a notification, and
    only after the transaction commits."""
    asker, answerer = make_client(), make_client()
    question = asker.post("/api/questions", json={"body": unique("Push me a notification")}).json()

    with asker.websocket_connect("/ws/notifications") as socket:
        assert socket.receive_json() == {"type": "ready"}
        answerer.post(f"/api/questions/{question['id']}/answers", json={"body": unique("An answer")})
        assert socket.receive_json() == {"type": "notifications"}

    # The signal carries no content: the browser re-reads the real list.
    unread = asker.get("/api/notifications").json()
    assert unread["unread"] >= 1


def test_notification_socket_requires_a_profile_cookie(app_modules):
    """A browser can only ever subscribe to its own notifications."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    stranger = TestClient(app_modules)  # no profile cookie established
    with pytest.raises(WebSocketDisconnect):
        with stranger.websocket_connect("/ws/notifications") as socket:
            socket.receive_json()


def test_feed_and_question_deletion(make_client):
    """The home feed shows latest team knowledge with groups collapsed, and an
    asker can delete a mistaken question only while nobody has answered."""
    author, other = make_client(), make_client()
    fact = f"Feed item rho-{uuid.uuid4().hex[:8]} exists for the feed test."
    author.post("/api/capture", data={"body": fact})
    other.post("/api/capture", data={"body": fact})  # duplicate -> one feed entry

    feed = other.get("/api/feed").json()
    matches = [i for i in feed if f"rho-" in i["body"] and fact.split()[2] in i["body"]]
    assert len(matches) == 1, "a corroboration group appears once in the feed"
    assert feed[0]["created_at"] >= feed[-1]["created_at"], "newest first"
    assert all(i["visibility"] == "team" for i in feed)

    # Question deletion: only the asker, and only while unanswered.
    question = author.post("/api/questions", json={"body": unique("Mistaken question")}).json()
    assert other.delete(f"/api/questions/{question['id']}").status_code == 403
    assert author.delete(f"/api/questions/{question['id']}").status_code == 200
    assert author.get(f"/api/questions/{question['id']}").status_code == 404

    answered = author.post("/api/questions", json={"body": unique("Real question")}).json()
    other.post(f"/api/questions/{answered['id']}/answers", json={"body": unique("An answer")})
    refused = author.delete(f"/api/questions/{answered['id']}")
    assert refused.status_code == 400, "a question with answers must survive"
    assert author.get(f"/api/questions/{answered['id']}").status_code == 200


def test_search_reports_matched_concepts(make_client, admin_client):
    """Search names the concepts the query mentions, so the graph can focus."""
    suffix = uuid.uuid4().hex[:6]
    concept = admin_client.post(
        "/api/admin/concepts", json={"name": f"Sigma{suffix}", "aliases": [f"sig{suffix}"]}
    ).json()
    client = make_client()
    results = client.get("/api/search", params={"q": f"what is sig{suffix} exactly"}).json()
    assert results["concepts"] == [{"id": concept["id"], "name": f"Sigma{suffix}"}]
    assert client.get("/api/search", params={"q": "nothing relevant here"}).json()["concepts"] == []


def test_scratchpad_private_and_share_selection(make_client):
    """W5+W6: scratchpad invisible to others; sharing exposes only the selection."""
    owner, other = make_client(), make_client()
    secret = uuid.uuid4().hex[:10]
    shared_fact = f"Public fact epsilon-{uuid.uuid4().hex[:8]} lives in the runbook."
    content = f"private-secret-{secret} stays private\n{shared_fact}\n"

    pad = owner.get("/api/scratchpad").json()["default"]
    owner.put(f"/api/scratchpad/{pad['id']}", json={"content": content})

    # Owner finds it; the other profile cannot see or search it.
    assert owner.get(f"/api/scratchpad/{pad['id']}/find", params={"q": secret}).json()["matches"]
    assert other.get(f"/api/scratchpad/{pad['id']}/find", params={"q": secret}).status_code == 404
    assert other.get("/api/search", params={"q": secret}).json()["scratchpad"] == []
    assert other.get("/api/search", params={"q": secret}).json()["items"] == []
    # The owner's own search does include their scratchpad.
    assert owner.get("/api/search", params={"q": secret}).json()["scratchpad"]

    # Share only the selected line.
    shared = owner.post(f"/api/scratchpad/{pad['id']}/share", json={"text": shared_fact}).json()
    assert shared["item"]["visibility"] == "team"

    other_results = other.get("/api/search", params={"q": shared_fact.split()[2]}).json()
    assert any(i["id"] == shared["item"]["id"] for i in other_results["items"])
    # The rest of the pad still is not exposed.
    assert other.get("/api/search", params={"q": secret}).json()["items"] == []


def test_corroboration_grouping_and_spam(make_client):
    """W7: similar contributions group; same-profile duplicates earn nothing extra."""
    a, b, reader = make_client(), make_client(), make_client()
    fact = f"Feed omega-{uuid.uuid4().hex[:8]} is processed through the staging layer before load."
    first = a.post("/api/capture", data={"body": fact}).json()
    assert first["corroboration"]["contributors"] == 1

    # Same profile reposts near-identical content: stored, grouped, no extra credit.
    spam = a.post("/api/capture", data={"body": fact + "!"}).json()
    assert spam["corroboration"]["group_size"] == 2
    assert spam["corroboration"]["contributors"] == 1

    # Independent profile corroborates.
    second = b.post("/api/capture", data={"body": fact}).json()
    assert second["corroboration"]["contributors"] == 2
    assert second["corroboration"]["group_size"] == 3

    # Search collapses the group into one result with combined signals.
    results = reader.get("/api/search", params={"q": fact.split()[1]}).json()
    hits = [i for i in results["items"] if i["group_id"] == second["item"]["group_id"]]
    assert len(hits) == 1
    assert hits[0]["contributors"] == 2

    # Helping the group pays each unique contributor once; a's duplicate pays once.
    reader.post(f"/api/items/{second['item']['id']}/helped")
    assert a.get("/api/profile").json()["totals"]["helped"] == 1
    assert b.get("/api/profile").json()["totals"]["helped"] == 1
    # Marking the duplicate item too adds nothing for the same reader.
    reader.post(f"/api/items/{spam['item']['id']}/helped")
    assert a.get("/api/profile").json()["totals"]["helped"] == 1

    # Dissimilar content must stay separate.
    contradiction = b.post(
        "/api/capture", data={"body": f"Feed omega is NOT processed through staging; it loads directly."}
    ).json()
    assert contradiction["item"]["group_id"] != second["item"]["group_id"]


def test_helped_stays_idempotent_across_group_formation(make_client):
    """Regression: a mark placed before the item joins a corroboration group
    must not be payable again after grouping (PRD 11 idempotency, PRD 7 spam)."""
    author, reader = make_client(), make_client()
    fact = f"Feed sigma-{uuid.uuid4().hex[:8]} loads after the staging checkpoint completes."
    first = author.post("/api/capture", data={"body": fact}).json()["item"]

    assert reader.post(f"/api/items/{first['id']}/helped").json()["created"] is True
    assert author.get("/api/profile").json()["totals"]["helped"] == 1

    # The author reposts near-identical content, forming a group with the marked item.
    duplicate = author.post("/api/capture", data={"body": fact + "."}).json()
    assert duplicate["corroboration"]["group_size"] == 2

    # Re-marking either member must not create a second event for the same author.
    assert reader.post(f"/api/items/{first['id']}/helped").json()["created"] is False
    assert reader.post(f"/api/items/{duplicate['item']['id']}/helped").json()["created"] is False
    assert author.get("/api/profile").json()["totals"]["helped"] == 1

    # The UI state agrees: both group members render as already marked.
    detail = reader.get(f"/api/items/{duplicate['item']['id']}").json()
    assert detail["marked_helped"] is True


def test_document_upload_search_open_exact_passage(make_client):
    """W8: uploaded document is searchable and opens at the exact passage."""
    client = make_client()
    marker = uuid.uuid4().hex[:10]
    text = (
        "Introduction paragraph.\n\n"
        f"The kappa-{marker} control requires daily sign-off by operations.\n\n"
        "Closing paragraph.\n"
    )
    r = client.post(
        "/api/documents",
        files={"file": ("controls.txt", io.BytesIO(text.encode()), "text/plain")},
    )
    assert r.status_code == 200
    doc = r.json()

    results = client.get("/api/search", params={"q": f"kappa-{marker}"}).json()
    assert results["documents"], "document passage should match"
    hit = results["documents"][0]
    assert hit["document_id"] == doc["id"]
    assert hit["locator"] == "Line 3"

    detail = client.get(f"/api/documents/{doc['id']}").json()
    passage = next(p for p in detail["passages"] if p["id"] == hit["id"])
    assert f"kappa-{marker}" in passage["text"]

    # Share the passage as team knowledge with the locator attached.
    shared = client.post(f"/api/passages/{hit['id']}/share", json={}).json()
    assert shared["item"]["source_passage_id"] == hit["id"]

    # Unsupported/executable files are rejected safely.
    bad = client.post(
        "/api/documents", files={"file": ("run.exe", io.BytesIO(b"MZ\x90"), "application/x-msdownload")}
    )
    assert bad.status_code == 400
    fake_pdf = client.post(
        "/api/documents", files={"file": ("fake.pdf", io.BytesIO(b"not a pdf"), "application/pdf")}
    )
    assert fake_pdf.status_code == 400


def test_graph_never_exposes_private_content(make_client, admin_client):
    """W9: local/global graph results do not expose private scratchpad content."""
    owner, other = make_client(), make_client()
    concept_name = f"SecretSys{uuid.uuid4().hex[:6]}"
    concept = admin_client.post(
        "/api/admin/concepts", json={"name": concept_name, "aliases": []}
    ).json()

    pad = owner.get("/api/scratchpad").json()["default"]
    owner.put(
        f"/api/scratchpad/{pad['id']}",
        json={"content": f"{concept_name} internal password rotation is manual."},
    )

    graph = other.get("/api/graph/local", params={"concept_id": concept["id"]}).json()
    assert len(graph["nodes"]) == 1  # only the concept itself
    concepts = other.get("/api/graph/concepts").json()
    entry = next(c for c in concepts if c["id"] == concept["id"])
    assert entry["mentions"] == 0

    # A team mention appears for everyone.
    owner.post("/api/capture", data={"body": f"{concept_name} is owned by the platform team."})
    graph = other.get("/api/graph/local", params={"concept_id": concept["id"]}).json()
    assert any(n["type"] == "item" for n in graph["nodes"])


def test_expertise_routing(make_client, admin_client):
    """W10: deterministic alias matching routes a question to the mapped expert."""
    asker, expert = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    concept = admin_client.post(
        "/api/admin/concepts",
        json={"name": f"Olymp{suffix}", "aliases": [f"oly-{suffix}"]},
    ).json()
    expert_id = expert.get("/api/profile").json()["id"]
    admin_client.post(
        "/api/admin/expertise", json={"profile_id": expert_id, "concept_id": concept["id"]}
    )

    question = asker.post(
        "/api/questions", json={"body": f"Why is the oly-{suffix} feed late today?"}
    ).json()

    # The UI never filters server-side; it reads the `matches_me` flag the list
    # already returns and floats those questions to the top itself.
    queue = expert.get("/api/questions").json()
    assert any(q["id"] == question["id"] and q["matches_me"] for q in queue)
    notes = expert.get("/api/notifications").json()
    assert any(n["item_id"] == question["id"] for n in notes["notifications"])

    preview = admin_client.get(
        "/api/admin/routing-preview", params={"q": f"oly-{suffix} is failing"}
    ).json()
    assert f"Olymp{suffix}" in preview["detected"]


def test_admin_auth_gates(make_client, admin_client):
    """No self-service admin creation over HTTP; admin APIs require a session;
    a logged-in admin can add more admins."""
    anonymous = make_client()
    # The setup endpoint is gone: accounts are created with `manage.py create-admin`.
    intruder = {"username": "x_intruder", "password": "password123"}
    assert anonymous.post("/api/admin/setup", json=intruder).status_code != 200
    # ...and nothing was created by trying.
    assert anonymous.post("/api/auth/login", json=intruder).status_code == 401
    assert anonymous.get("/api/admin/concepts").status_code == 401
    assert anonymous.get("/api/admin/expertise").status_code == 401
    # Anonymous visitors are told nothing about whether admin accounts exist.
    state = anonymous.get("/api/auth/state").json()
    assert state == {"signed_in": False, "username": None, "is_admin": False}

    # Signing yourself up is a contributor account and never an admin one.
    joiner = make_client()
    r = joiner.post("/api/auth/signup", json={"username": f"joiner{uuid.uuid4().hex[:5]}", "password": "a-good-password"})
    assert r.status_code == 200 and r.json()["is_admin"] is False
    assert joiner.get("/api/auth/state").json()["is_admin"] is False
    assert joiner.get("/api/admin/concepts").status_code == 401

    r = admin_client.post(
        "/api/admin/admins", json={"username": f"second{uuid.uuid4().hex[:5]}", "password": "another-pass-1"}
    )
    assert r.status_code == 200

    assert anonymous.post(
        "/api/auth/login", json={"username": "rootadmin", "password": "wrong-password"}
    ).status_code == 401


def _concept(admin_client, name: str, aliases: list[str] | None = None) -> dict:
    return admin_client.post(
        "/api/admin/concepts", json={"name": name, "aliases": aliases or []}
    ).json()


def _link_between(admin_client, a: str, b: str) -> dict | None:
    links = admin_client.get("/api/graph/links", params={"concept_id": a}).json()
    return next((l for l in links if b in (l["src_id"], l["dst_id"])), None)


def test_vocabulary_is_a_single_source_of_truth(make_client, admin_client):
    """A word belongs to exactly one concept: names and aliases share one
    namespace, case-insensitively, so duplicates are refused instead of
    silently resolving to whichever concept happens to be found first."""
    suffix = uuid.uuid4().hex[:6]
    first = admin_client.post(
        "/api/admin/concepts", json={"name": f"Optima{suffix}", "aliases": [f"opt{suffix}"]}
    ).json()
    assert first["name"] == f"Optima{suffix}"
    assert first["aliases"] == [f"opt{suffix}"]

    # A second concept cannot claim the first one's name...
    clash = admin_client.post(
        "/api/admin/concepts", json={"name": f"Payments{suffix}", "aliases": [f"Optima{suffix}"]}
    )
    assert clash.status_code == 400
    assert f"Optima{suffix}" in clash.json()["detail"]

    # ...nor a case variant of it, as a name or an alias.
    assert admin_client.post(
        "/api/admin/concepts", json={"name": f"optima{suffix}".lower(), "aliases": []}
    ).status_code == 400
    assert admin_client.post(
        "/api/admin/concepts", json={"name": f"Other{suffix}", "aliases": [f"OPT{suffix}"]}
    ).status_code == 400

    # A concept can still edit its own words.
    updated = admin_client.put(
        f"/api/admin/concepts/{first['id']}",
        json={"name": f"Optima{suffix}", "aliases": [f"opt{suffix}", f"opti{suffix}"]},
    )
    assert updated.status_code == 200
    assert updated.json()["aliases"] == [f"opt{suffix}", f"opti{suffix}"]

    # A concept's identity is its canonical term, so a blank name is refused —
    # even when aliases would otherwise make the term set non-empty. Accepting
    # it once left a nameless concept and released the old name.
    assert admin_client.post(
        "/api/admin/concepts", json={"name": "   ", "aliases": [f"ghost{suffix}"]}
    ).status_code == 400
    assert admin_client.put(
        f"/api/admin/concepts/{first['id']}", json={"name": " ", "aliases": [f"alt{suffix}"]}
    ).status_code == 400
    # The refused update must leave the concept entirely untouched: same name,
    # same aliases, and its name still reserved rather than released.
    intact = next(
        c for c in admin_client.get("/api/admin/concepts").json() if c["id"] == first["id"]
    )
    assert intact["name"] == f"Optima{suffix}"
    assert intact["aliases"] == [f"opt{suffix}", f"opti{suffix}"]
    assert admin_client.post(
        "/api/admin/concepts", json={"name": f"Optima{suffix}", "aliases": []}
    ).status_code == 400
    assert_one_canonical_term_per_concept()


def assert_one_canonical_term_per_concept() -> None:
    """Every concept has exactly one canonical term — the invariant the whole
    single-vocabulary design rests on."""
    from sqlalchemy import text as sql_text

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        broken = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM concepts c WHERE ("
                "  SELECT COUNT(*) FROM concept_terms t"
                "  WHERE t.concept_id = c.id AND t.is_canonical = 1) != 1"
            )
        ).scalar()
        duplicates = db.execute(
            sql_text("SELECT COUNT(*) - COUNT(DISTINCT term) FROM concept_terms")
        ).scalar()
    finally:
        db.close()
    assert broken == 0, f"{broken} concept(s) without exactly one canonical term"
    assert duplicates == 0, "a term is owned by more than one concept"


def test_tags_are_rebuilt_when_the_vocabulary_changes(make_client, admin_client):
    """Tags are derived data: renaming or removing a word must not leave content
    tagged with a concept its text no longer mentions, and a concept created
    after a document was uploaded must still find that document."""
    author = make_client()
    suffix = uuid.uuid4().hex[:6]
    concept = admin_client.post(
        "/api/admin/concepts", json={"name": f"Falcon{suffix}", "aliases": [f"fal{suffix}"]}
    ).json()
    mentions = lambda: next(
        c["mentions"] for c in author.get("/api/graph/concepts").json() if c["id"] == concept["id"]
    )

    author.post("/api/capture", data={"body": f"The Falcon{suffix} service restarts on Sundays."})
    author.post("/api/capture", data={"body": f"Check fal{suffix} before the release."})
    assert mentions() == 2

    # Removing the alias must drop the tag that only the alias justified.
    admin_client.put(
        f"/api/admin/concepts/{concept['id']}", json={"name": f"Falcon{suffix}", "aliases": []}
    )
    assert mentions() == 1

    # Renaming must drop tags whose text no longer mentions the concept at all.
    admin_client.put(
        f"/api/admin/concepts/{concept['id']}", json={"name": f"Peregrine{suffix}", "aliases": []}
    )
    assert mentions() == 0

    # A concept defined after a document was uploaded still tags that document.
    marker = f"Kestrel{uuid.uuid4().hex[:6]}"
    author.post(
        "/api/documents",
        files={"file": ("ops.txt", io.BytesIO(f"The {marker} ledger reconciles daily.".encode()), "text/plain")},
    )
    late = admin_client.post("/api/admin/concepts", json={"name": marker, "aliases": []}).json()
    late_mentions = next(
        c["mentions"] for c in author.get("/api/graph/concepts").json() if c["id"] == late["id"]
    )
    assert late_mentions == 1, "document passages must be tagged by the backfill too"


def test_word_forms_reach_their_concept(make_client, admin_client):
    """Writing "emulate" or "emulators" means the Emulation concept. Matching
    stays deterministic: a shared stem, never a guess."""
    author, reader = make_client(), make_client()
    tag = uuid.uuid4().hex[:6]
    root = f"zorb{tag}"                      # a unique but well-formed English root
    concept = admin_client.post(
        "/api/admin/concepts", json={"name": f"{root}ation", "aliases": []}
    ).json()
    mentions = lambda: next(
        c["mentions"] for c in reader.get("/api/graph/concepts").json() if c["id"] == concept["id"]
    )

    for body in [
        f"You can {root}ate the old cartridges on a laptop.",
        f"Two {root}ators were compared for frame timing.",
        f"She spent the weekend {root}ating an arcade board.",
    ]:
        author.post("/api/capture", data={"body": body})
    assert mentions() == 3, "verb, agent noun and gerund all reach the concept"

    # The search box understands the same forms.
    results = reader.get("/api/search", params={"q": f"how do I {root}ate a cartridge"}).json()
    assert results["concepts"] and results["concepts"][0]["id"] == concept["id"]
    assert results["items"], "search finds the content written in another word form"

    # An unrelated word sharing no stem is left alone.
    author.post("/api/capture", data={"body": f"The {root}ic festival has nothing to do with it."})
    assert mentions() == 3


def test_a_shared_stem_is_refused_rather_than_guessed(make_client, admin_client):
    """Two concepts whose words reduce to the same stem keep exact matching
    only — the loose match is dropped rather than handed to whichever was
    found first."""
    author, reader = make_client(), make_client()
    tag = uuid.uuid4().hex[:6]
    first = admin_client.post("/api/admin/concepts", json={"name": f"blorp{tag}ing", "aliases": []}).json()
    second = admin_client.post("/api/admin/concepts", json={"name": f"blorp{tag}ed", "aliases": []}).json()

    author.post("/api/capture", data={"body": f"blorp{tag}ing is a community tradition."})
    counts = {c["id"]: c["mentions"] for c in reader.get("/api/graph/concepts").json()}
    assert counts[first["id"]] == 1, "the exact word still tags its own concept"
    assert counts[second["id"]] == 0, "the shared stem must not tag the other concept"


def test_deleting_a_concept_leaves_nothing_behind(make_client, admin_client):
    """Terms, tags, expertise mappings and links all go with the concept."""
    author = make_client()
    suffix = uuid.uuid4().hex[:6]
    concept = admin_client.post(
        "/api/admin/concepts", json={"name": f"Zephyr{suffix}", "aliases": [f"zeph{suffix}"]}
    ).json()
    author.post("/api/capture", data={"body": f"Zephyr{suffix} handles the overnight sweep."})
    profile_id = author.get("/api/profile").json()["id"]
    admin_client.post(
        "/api/admin/expertise", json={"profile_id": profile_id, "concept_id": concept["id"]}
    )

    assert admin_client.delete(f"/api/admin/concepts/{concept['id']}").status_code == 200
    assert all(c["id"] != concept["id"] for c in author.get("/api/graph/concepts").json())
    assert all(
        concept["id"] not in [a["concept_id"] for a in row["areas"]]
        for row in admin_client.get("/api/admin/expertise").json()
    )
    # The word is free again now that nothing owns it.
    assert admin_client.post(
        "/api/admin/concepts", json={"name": f"Zephyr{suffix}", "aliases": []}
    ).status_code == 200


def test_link_discovery_review_and_reversal(make_client, admin_client):
    """Detected links are suggested/dashed, approving makes them confirmed/solid,
    rejecting hides them from the map while the count keeps rising, and the
    decision is reversible."""
    author, reader = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    left = _concept(admin_client, f"Alpha{suffix}")
    right = _concept(admin_client, f"Beta{suffix}")

    # Nothing mentions both concepts yet, so there is nothing to suggest.
    author.post("/api/capture", data={"body": f"Alpha{suffix} runs on its own. {uuid.uuid4().hex}"})
    assert _link_between(admin_client, left["id"], right["id"]) is None

    # One contribution stating the relationship is enough to suggest it: real
    # prose says a thing once, and a suggestion is dashed and reviewable.
    author.post("/api/capture", data={"body": f"Alpha{suffix} feeds Beta{suffix} nightly. {uuid.uuid4().hex}"})
    link = _link_between(admin_client, left["id"], right["id"])
    assert link["state"] == "suggested"
    assert link["occurrence_count"] == 1
    assert link["type_name"] == "related to"

    # More evidence raises the count without changing the state.
    for _ in range(2):
        author.post("/api/capture", data={"body": f"Alpha{suffix} and Beta{suffix} share a window. {uuid.uuid4().hex}"})
    assert _link_between(admin_client, left["id"], right["id"])["occurrence_count"] == 3

    # Suggested links are dashed and visible to everyone.
    graph = reader.get("/api/graph/local", params={"concept_id": left["id"]}).json()
    edge = next(e for e in graph["edges"] if e["link_id"] == link["id"])
    assert edge["style"] == "dashed"

    # Approving promotes it to solid.
    admin_client.patch(f"/api/graph/links/{link['id']}", json={"state": "confirmed"})
    graph = reader.get("/api/graph/local", params={"concept_id": left["id"]}).json()
    assert next(e for e in graph["edges"] if e["link_id"] == link["id"])["style"] == "solid"

    # Rejecting removes it from the local and global maps.
    admin_client.patch(
        f"/api/graph/links/{link['id']}", json={"state": "rejected", "note": "Coincidence"}
    )
    graph = reader.get("/api/graph/local", params={"concept_id": left["id"]}).json()
    assert all(e["link_id"] != link["id"] for e in graph["edges"])
    global_edges = reader.get("/api/graph/global").json()["edges"]
    assert not [e for e in global_edges if {e["source"], e["target"]} == {left["id"], right["id"]}]

    # ...but it stays visible to admins and keeps counting new evidence.
    author.post("/api/capture", data={"body": f"Alpha{suffix} to Beta{suffix} again. {uuid.uuid4().hex}"})
    rejected = _link_between(admin_client, left["id"], right["id"])
    assert rejected["state"] == "rejected"
    assert rejected["occurrence_count"] == 4
    assert rejected["review_note"] == "Coincidence"
    assert rejected["reviewed_by"] == "rootadmin"

    # Drill-down shows the real contributions behind it.
    evidence = reader.get(f"/api/graph/links/{link['id']}/evidence").json()
    assert evidence["occurrence_count"] == 4
    assert len(evidence["items"]) == 4

    # Re-approving restores it.
    admin_client.patch(f"/api/graph/links/{link['id']}", json={"state": "confirmed"})
    graph = reader.get("/api/graph/local", params={"concept_id": left["id"]}).json()
    edge = next(e for e in graph["edges"] if e["link_id"] == link["id"])
    assert edge["style"] == "solid"
    # Regression: the "why connected" sentence tracks the live count after review
    # rather than freezing at the count when the link was first detected.
    assert "4 team entries" in edge["evidence"]
    author.post("/api/capture", data={"body": f"Alpha{suffix} with Beta{suffix} once more. {uuid.uuid4().hex}"})
    graph = reader.get("/api/graph/local", params={"concept_id": left["id"]}).json()
    assert "5 team entries" in next(e for e in graph["edges"] if e["link_id"] == link["id"])["evidence"]


def test_link_admin_gating_and_manual_links(make_client, admin_client):
    """Only admins mutate links; manual links require a note and carry it as evidence."""
    anonymous = make_client()
    suffix = uuid.uuid4().hex[:6]
    left = _concept(admin_client, f"Gamma{suffix}")
    right = _concept(admin_client, f"Delta{suffix}")

    assert anonymous.get("/api/graph/links").status_code == 401
    assert anonymous.post(
        "/api/graph/links",
        json={"src_id": left["id"], "dst_id": right["id"], "type_id": "x", "note": "n"},
    ).status_code == 401

    feeds = admin_client.post("/api/admin/relationship-types", json={"name": f"feeds{suffix}"}).json()

    created = admin_client.post(
        "/api/graph/links",
        json={
            "src_id": left["id"],
            "dst_id": right["id"],
            "type_id": feeds["id"],
            "note": "Confirmed with the platform team",
        },
    ).json()
    assert created["state"] == "confirmed"
    assert "Confirmed with the platform team" in created["evidence"]

    # A second link between the same pair is refused; edit the existing one.
    duplicate = admin_client.post(
        "/api/graph/links",
        json={"src_id": right["id"], "dst_id": left["id"], "type_id": feeds["id"], "note": "again"},
    )
    assert duplicate.status_code == 400

    # A manual link renders solid with its own label.
    graph = anonymous.get("/api/graph/local", params={"concept_id": left["id"]}).json()
    edge = next(e for e in graph["edges"] if e["link_id"] == created["id"])
    assert edge["style"] == "solid"
    assert edge["label"] == f"feeds{suffix}"

    # 'corroborates' is generated between contributions and cannot label a
    # concept link, on either the create or the update path.
    builtin = next(
        t for t in admin_client.get("/api/graph/relationship-types").json() if not t["selectable"]
    )
    assert admin_client.patch(
        f"/api/graph/links/{created['id']}", json={"type_id": builtin["id"]}
    ).status_code == 400

    assert anonymous.delete(f"/api/graph/links/{created['id']}").status_code == 401
    assert admin_client.delete(f"/api/graph/links/{created['id']}").status_code == 200


def test_relationship_type_vocabulary(make_client, admin_client):
    """Types can be renamed (changing the map), deleted only when unused, and
    built-ins are protected."""
    suffix = uuid.uuid4().hex[:6]
    left = _concept(admin_client, f"Eps{suffix}")
    right = _concept(admin_client, f"Zeta{suffix}")
    rtype = admin_client.post("/api/admin/relationship-types", json={"name": f"owns{suffix}"}).json()

    link = admin_client.post(
        "/api/graph/links",
        json={"src_id": left["id"], "dst_id": right["id"], "type_id": rtype["id"], "note": "why"},
    ).json()

    # Usage is reported so the UI can warn before renaming.
    listed = admin_client.get("/api/graph/relationship-types").json()
    assert next(t for t in listed if t["id"] == rtype["id"])["usage"] == 1

    # Deleting a type in use is refused and names the count.
    refused = admin_client.delete(f"/api/admin/relationship-types/{rtype['id']}")
    assert refused.status_code == 400
    assert "used by 1 link" in refused.json()["detail"]

    # Renaming updates the existing map immediately.
    admin_client.put(f"/api/admin/relationship-types/{rtype['id']}", json={"name": f"stewards{suffix}"})
    graph = make_client().get("/api/graph/local", params={"concept_id": left["id"]}).json()
    assert next(e for e in graph["edges"] if e["link_id"] == link["id"])["label"] == f"stewards{suffix}"

    # Once unused it can be deleted.
    admin_client.delete(f"/api/graph/links/{link['id']}")
    assert admin_client.delete(f"/api/admin/relationship-types/{rtype['id']}").status_code == 200

    # Built-ins are protected either way.
    builtin = next(t for t in admin_client.get("/api/graph/relationship-types").json() if t["is_builtin"])
    assert admin_client.delete(f"/api/admin/relationship-types/{builtin['id']}").status_code == 400
    assert admin_client.put(
        f"/api/admin/relationship-types/{builtin['id']}", json={"name": "renamed"}
    ).status_code == 400


def test_links_never_expose_private_content(make_client, admin_client):
    """Private scratchpad text contributes no links, counts, or evidence."""
    owner, other = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    left = _concept(admin_client, f"Priv{suffix}")
    right = _concept(admin_client, f"Sec{suffix}")

    pad = owner.get("/api/scratchpad").json()["default"]
    owner.put(
        f"/api/scratchpad/{pad['id']}",
        json={"content": f"Priv{suffix} and Sec{suffix} rotate together\n" * 5},
    )
    assert _link_between(admin_client, left["id"], right["id"]) is None

    # Team content creates the link; the private text adds nothing to its evidence.
    for _ in range(3):
        other.post("/api/capture", data={"body": f"Priv{suffix} pairs with Sec{suffix}. {uuid.uuid4().hex}"})
    link = _link_between(admin_client, left["id"], right["id"])
    assert link["occurrence_count"] == 3
    evidence = admin_client.get(f"/api/graph/links/{link['id']}/evidence").json()
    assert len(evidence["items"]) == 3
    assert all("rotate together" not in i["body"] for i in evidence["items"])


def test_manage_command_creates_admin(make_client):
    """`python manage.py create-admin` creates a working account on the server."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    manage = Path(__file__).resolve().parents[2] / "manage.py"
    username = f"deployadmin{uuid.uuid4().hex[:6]}"
    password = "deploy-time-pw"

    result = subprocess.run(
        [sys.executable, str(manage), "create-admin", "--username", username],
        input=password + "\n",
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert result.returncode == 0, result.stderr
    assert f"Created admin '{username}'" in result.stdout

    client = make_client()
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    assert client.get("/api/admin/concepts").status_code == 200

    # Re-running for the same username fails instead of silently replacing the account.
    repeat = subprocess.run(
        [sys.executable, str(manage), "create-admin", "--username", username],
        input=password + "\n",
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert repeat.returncode != 0
    assert "already exists" in repeat.stderr


def test_reset_database_command(tmp_path):
    """`reset-database` refuses without confirmation, and otherwise rebuilds the
    current schema from whatever version was there before."""
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    alembic_bin = Path(sys.executable).parent / "alembic"
    db = tmp_path / "t.sqlite3"
    env = {**os.environ, "MDS_DATA_DIR": str(tmp_path), "MDS_DATABASE_URL": f"sqlite:///{db}"}

    # Start from an older revision to prove the reset does not depend on it.
    subprocess.run(
        [str(alembic_bin), "-c", str(root / "backend/alembic.ini"), "upgrade", "0001"],
        env=env, check=True, capture_output=True,
    )
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, token_hash, created_at) VALUES ('p','h',datetime('now'))")
    con.commit()
    con.close()
    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    (uploads / "kept.txt").write_text("x")

    command = [sys.executable, str(root / "manage.py"), "reset-database"]
    refused = subprocess.run(
        command, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert refused.returncode != 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1, "nothing may be deleted"
    con.close()
    assert (uploads / "kept.txt").exists()

    done = subprocess.run(command + ["--yes"], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr

    head = ScriptDirectory.from_config(
        Config(str(root / "backend/alembic.ini"))
    ).get_current_head()
    con = sqlite3.connect(db)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head
    assert con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM relationship_types").fetchone()[0] == 2
    con.close()
    assert not (uploads / "kept.txt").exists()


def test_correction_lifecycle(make_client, admin_client):
    """Correction proposed, adopted by original author, impact once, history kept."""
    author, corrector = make_client(), make_client()
    item = author.post("/api/capture", data={"body": unique("The cutoff is 0800 ET")}).json()["item"]
    correction = corrector.post(
        f"/api/items/{item['id']}/corrections", json={"body": "The cutoff moved to 0830 ET."}
    ).json()

    # A random profile cannot adopt.
    assert corrector.post(f"/api/corrections/{correction['id']}/adopt").status_code == 403
    r = author.post(f"/api/corrections/{correction['id']}/adopt").json()
    assert r["impact_created"] is True
    assert author.post(f"/api/corrections/{correction['id']}/adopt").json()["impact_created"] is False

    detail = author.get(f"/api/items/{item['id']}").json()
    assert detail["corrections"][0]["correction_state"] == "adopted"
    assert len(detail["revisions"]) == 1
    assert corrector.get("/api/profile").json()["totals"]["corrections"] == 1


def test_unknown_api_path_is_a_json_404_not_the_spa(make_client):
    """A path under a namespace the server owns must 404 in JSON. Serving
    index.html here returns 200 and HTML to a caller expecting JSON, so a
    removed or mistyped endpoint reads as working."""
    client = make_client()
    for path in ("/api/leaderboard", "/api/does-not-exist", "/ws/nope"):
        r = client.get(path)
        assert r.status_code == 404, f"{path} -> {r.status_code}"
        assert r.headers["content-type"].startswith("application/json"), f"{path} -> {r.headers['content-type']}"

    # The SPA itself must still be served for real client-side routes.
    assert client.get("/leaderboard").status_code == 200
    assert client.get("/api/feed").status_code == 200


def test_you_are_never_routed_your_own_question(make_client, admin_client):
    """The person who asked is not one of the people who should answer. Both
    routing surfaces — the 'needs your expertise' flag on the list and the
    suggested-experts line on the detail — must exclude the asker, while still
    routing the question to every other mapped expert."""
    asker, other_expert = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    concept = _concept(admin_client, f"Kepler{suffix}", [f"kep-{suffix}"])
    for client in (asker, other_expert):
        admin_client.post(
            "/api/admin/expertise",
            json={"profile_id": client.get("/api/profile").json()["id"], "concept_id": concept["id"]},
        )
    asker.put("/api/profile", json={"display_name": f"Asker {suffix}"})
    other_expert.put("/api/profile", json={"display_name": f"Other {suffix}"})

    question = asker.post(
        "/api/questions", json={"body": f"How do I restart the kep-{suffix} pipeline?"}
    ).json()

    mine = next(q for q in asker.get("/api/questions").json() if q["id"] == question["id"])
    assert mine["matches_me"] is False, "the asker was told their own question needs their expertise"

    theirs = next(q for q in other_expert.get("/api/questions").json() if q["id"] == question["id"])
    assert theirs["matches_me"] is True, "a real expert stopped being routed the question"

    detail = other_expert.get(f"/api/questions/{question['id']}").json()
    assert f"Asker {suffix}" not in detail["suggested_experts"], "the asker was suggested as their own expert"
    assert f"Other {suffix}" in detail["suggested_experts"]


def _signup(client, name: str) -> dict:
    r = client.post("/api/auth/signup", json={"username": name, "password": "a-good-password"})
    assert r.status_code == 200, r.text
    return r.json()


def test_signing_in_changes_who_the_app_thinks_you_are(make_client, admin_client):
    """Admin login and contributor identity used to be separate systems, so a
    signed-in admin's own contributions were credited to a hex code."""
    me = admin_client.get("/api/profile").json()
    assert me["label"] == ADMIN_CREDENTIALS["username"]
    assert me["verified"] is True

    body = unique("The admin wrote this down")
    admin_client.post("/api/capture", data={"body": body})
    feed = admin_client.get("/api/feed").json()
    mine = next(i for i in feed if body in i["body"])
    assert mine["author"] == ADMIN_CREDENTIALS["username"]
    assert "Browser profile" not in mine["author"]


def test_an_account_keeps_the_work_you_did_before_you_had_one(make_client):
    """Use it first, sign up later: the point of anonymous access is lost if
    signing up orphans everything you already contributed."""
    person = make_client()
    body = unique("Written before I had an account")
    person.post("/api/capture", data={"body": body})
    anon = person.get("/api/profile").json()
    assert anon["verified"] is False

    name = f"claimer{uuid.uuid4().hex[:6]}"
    _signup(person, name)

    now = person.get("/api/profile").json()
    assert now["id"] == anon["id"], "signing up started a new identity instead of claiming"
    assert now["label"] == name and now["verified"] is True
    assert now["totals"]["shared"] == anon["totals"]["shared"]
    mine = next(i for i in person.get("/api/feed").json() if body in i["body"])
    assert mine["author"] == name and mine["is_mine"] is True


def test_only_the_first_sign_in_on_a_machine_claims_its_anonymous_work(make_client):
    """A colleague signing in on your machine must not absorb your
    contributions. The claim is a one-shot per browser."""
    machine = make_client()
    body = unique("Work done anonymously on this machine")
    machine.post("/api/capture", data={"body": body})
    anon_id = machine.get("/api/profile").json()["id"]

    first = f"first{uuid.uuid4().hex[:6]}"
    _signup(machine, first)
    assert machine.get("/api/profile").json()["id"] == anon_id  # claimed

    machine.post("/api/auth/logout")
    second = f"second{uuid.uuid4().hex[:6]}"
    _signup(machine, second)
    after = machine.get("/api/profile").json()
    assert after["label"] == second
    assert after["id"] != anon_id, "the second person absorbed the first person's profile"
    assert after["totals"]["shared"] == 0, "the second person inherited someone else's contributions"

    # And the work is still the first person's.
    machine.post("/api/auth/logout")
    machine.post("/api/auth/login", json={"username": first, "password": "a-good-password"})
    assert machine.get("/api/profile").json()["id"] == anon_id


def test_the_claim_is_spent_even_when_the_first_sign_in_claims_nothing(make_client):
    """The one-shot is spent by the first sign-in on a machine, not by the first
    successful claim. Someone who already has a profile from another machine
    signs in here and claims nothing — and the anonymous work on this machine
    must still not fall to the next person who signs up on it.
    """
    elsewhere = make_client()
    visitor = f"visitor{uuid.uuid4().hex[:6]}"
    _signup(elsewhere, visitor)  # this account already owns a profile

    machine = make_client()
    body = unique("Anonymous work belonging to whoever uses this machine")
    machine.post("/api/capture", data={"body": body})
    anon = machine.get("/api/profile").json()
    assert anon["totals"]["shared"] == 1

    # The visitor signs in here. They keep their own profile; nothing is claimed.
    machine.post("/api/auth/login", json={"username": visitor, "password": "a-good-password"})
    assert machine.get("/api/profile").json()["id"] != anon["id"]
    machine.post("/api/auth/logout")

    # The next person to sign up on this machine must not inherit that work.
    latecomer = f"late{uuid.uuid4().hex[:6]}"
    _signup(machine, latecomer)
    got = machine.get("/api/profile").json()
    assert got["id"] != anon["id"], "a later sign-up absorbed the machine's anonymous profile"
    assert got["totals"]["shared"] == 0, "a later sign-up inherited someone else's contributions"


def test_expertise_can_only_be_routed_to_someone_with_an_account(make_client, admin_client):
    """An anonymous browser profile is unanswerable as an expert and its
    mapping would die with a cookie clear."""
    anonymous = make_client()
    anonymous.get("/api/profile")
    named = make_client()
    account = f"expert{uuid.uuid4().hex[:6]}"
    _signup(named, account)

    listed = admin_client.get("/api/admin/profiles").json()
    labels = [p["label"] for p in listed]
    assert account in labels
    assert not any(l.startswith("Browser profile") for l in labels), labels
    assert all(p["verified"] for p in listed)


def test_anyone_can_endorse_and_the_admin_sees_who_the_team_endorsed(make_client, admin_client):
    """Endorsement is the evidence an admin maps expertise from, so it cannot
    require the endorser to already be a mapped expert."""
    author, endorser = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    _concept(admin_client, f"Ledger{suffix}", [f"ldg-{suffix}"])
    item = author.post(
        "/api/capture", data={"body": f"The ldg-{suffix} close runs on the first working day."}
    ).json()["item"]

    r = endorser.post(f"/api/items/{item['id']}/endorse")
    assert r.status_code == 200, f"a plain teammate could not endorse: {r.text}"
    assert r.json()["created"] is True
    assert author.post(f"/api/items/{item['id']}/endorse").status_code == 400  # still not your own

    ranking = admin_client.get("/api/admin/endorsements").json()
    row = next(e for e in ranking if e["profile_id"] == item["author_id"])
    assert row["endorsements"] >= 1
    assert f"Ledger{suffix}" in row["topics"]


def test_a_new_concept_finds_links_in_content_that_already_exists(make_client, admin_client):
    """Discovery used to run only when somebody posted, so an admin who defined
    two concepts that already co-occur was shown an empty map."""
    author = make_client()
    suffix = uuid.uuid4().hex[:6]
    author.post(
        "/api/capture",
        data={"body": f"The nightly qux{suffix} job reconciles against the zar{suffix} ledger."},
    )
    first = _concept(admin_client, f"Qux{suffix}", [f"qux{suffix}"])
    # Nothing to pair with yet.
    assert _link_between(admin_client, first["id"], first["id"]) is None
    second = _concept(admin_client, f"Zar{suffix}", [f"zar{suffix}"])

    link = _link_between(admin_client, second["id"], first["id"])
    assert link is not None, "the two concepts co-occur in existing content but no link was found"
    assert link["occurrence_count"] >= 1


def test_the_author_can_fix_and_remove_their_own_contribution(make_client):
    """A typo used to be permanent and nothing could be taken back."""
    author, reader = make_client(), make_client()
    marker = uuid.uuid4().hex[:8]
    item = author.post("/api/capture", data={"body": f"The nightly job runs at 7am {marker}"}).json()["item"]

    r = author.put(f"/api/items/{item['id']}", json={"body": f"The nightly job runs at 7pm {marker}"})
    assert r.status_code == 200
    assert "7pm" in reader.get(f"/api/items/{item['id']}").json()["body"]
    # The corrected text is what the team now finds.
    found = reader.get("/api/search", params={"q": marker}).json()["items"]
    assert found and "7pm" in found[0]["body"] and "7am" not in found[0]["body"]

    assert reader.put(f"/api/items/{item['id']}", json={"body": "not mine to edit"}).status_code == 403
    assert reader.delete(f"/api/items/{item['id']}").status_code == 403

    assert author.delete(f"/api/items/{item['id']}").status_code == 200
    assert reader.get(f"/api/items/{item['id']}").status_code == 404
    assert not reader.get("/api/search", params={"q": marker}).json()["items"]


def test_deleting_your_own_contribution_cannot_destroy_a_teammates(make_client):
    """The rule the question delete has always had, applied to everything."""
    asker, answerer = make_client(), make_client()
    q = asker.post("/api/questions", json={"body": unique("Who owns the nightly job?")}).json()
    answerer.post(f"/api/questions/{q['id']}/answers", json={"body": "The platform team does."})

    r = asker.delete(f"/api/items/{q['id']}")
    assert r.status_code == 400
    assert "teammate" in r.json()["detail"]
    assert asker.get(f"/api/items/{q['id']}").status_code == 200


def test_the_uploader_can_delete_a_document_without_destroying_shared_excerpts(make_client):
    """Uploads used to be permanent; a wrong file was there forever."""
    uploader, reader = make_client(), make_client()
    marker = uuid.uuid4().hex[:8]
    doc = uploader.post(
        "/api/documents",
        files={"file": ("notes.txt", io.BytesIO(f"A passage worth keeping {marker}.\n".encode()), "text/plain")},
    ).json()
    passages = uploader.get(f"/api/documents/{doc['id']}").json()["passages"]
    shared = reader.post(f"/api/passages/{passages[0]['id']}/share", json={}).json()["item"]

    assert reader.delete(f"/api/documents/{doc['id']}").status_code == 403
    r = uploader.delete(f"/api/documents/{doc['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["kept_shared_excerpts"] == 1

    assert reader.get(f"/api/documents/{doc['id']}").status_code == 404
    assert all(d["id"] != doc["id"] for d in reader.get("/api/documents").json())
    # The excerpt someone shared is team knowledge and survives, with no link
    # to a file that no longer exists.
    kept = reader.get(f"/api/items/{shared['id']}").json()
    assert marker in kept["body"]
    assert kept["source_document_id"] is None


def test_signing_out_really_makes_you_anonymous_again(make_client):
    """Claiming a browser profile binds it to an account. If the browser cookie
    still resolved to it afterwards, signing out would leave you posting as the
    account you just signed out of."""
    person = make_client()
    person.post("/api/capture", data={"body": unique("Written while signed in")})
    name = f"leaver{uuid.uuid4().hex[:6]}"
    _signup(person, name)
    signed_in = person.get("/api/profile").json()
    assert signed_in["verified"] is True and signed_in["label"] == name

    person.post("/api/auth/logout")
    after = person.get("/api/profile").json()
    assert after["verified"] is False, "still shown as signed in after signing out"
    assert after["label"] != name, "still carrying the account's name after signing out"
    assert after["id"] != signed_in["id"], "still the account's profile after signing out"

    body = unique("Written after signing out")
    person.post("/api/capture", data={"body": body})
    posted = next(i for i in person.get("/api/feed").json() if body in i["body"])
    assert posted["author"] != name, "a signed-out person is still posting as the account"

    # And signing back in returns you to your own work.
    person.post("/api/auth/login", json={"username": name, "password": "a-good-password"})
    assert person.get("/api/profile").json()["id"] == signed_in["id"]


def test_the_notification_socket_knows_who_is_signed_in(make_client):
    """The socket identified people by the browser cookie while every REST route
    identifies them by the session. A signed-in person's profile has no browser
    token at all, so their socket was refused and they silently fell back to
    polling."""
    person = make_client()
    with person.websocket_connect("/ws/notifications") as ws:
        assert ws.receive_json()["type"] == "ready"  # anonymous still works

    _signup(person, f"socket{uuid.uuid4().hex[:6]}")
    with person.websocket_connect("/ws/notifications") as ws:
        assert ws.receive_json()["type"] == "ready", "a signed-in person cannot open the socket"


def test_more_than_one_person_can_endorse_the_same_contribution(make_client, admin_client):
    """Endorsement is the count an admin reads to decide who the experts are, so
    hiding the control after the first one capped every item at a single voice."""
    author, first, second = make_client(), make_client(), make_client()
    item = author.post("/api/capture", data={"body": unique("Restarting the sweep needs the lock released first")}).json()["item"]

    assert first.post(f"/api/items/{item['id']}/endorse").json()["created"] is True
    assert second.post(f"/api/items/{item['id']}/endorse").json()["created"] is True

    seen_by_third = make_client().get(f"/api/items/{item['id']}").json()
    assert seen_by_third["endorsements"] == 2
    assert seen_by_third["endorsed_by_me"] is False, "someone who has not endorsed is told they have"

    seen_by_first = first.get(f"/api/items/{item['id']}").json()
    assert seen_by_first["endorsed_by_me"] is True, "an endorser is offered the action again"

    ranking = admin_client.get("/api/admin/endorsements").json()
    assert next(e for e in ranking if e["profile_id"] == item["author_id"])["endorsements"] == 2


def test_deleting_a_question_that_carries_a_correction_does_not_explode(make_client):
    """`delete_question` predates `knowledge.delete_item` and cleans up less: it
    removes notifications and the row, but not the corrections, revisions or
    impact events that point at it, so the foreign keys refuse and the caller
    gets a 500 instead of an answer."""
    asker, reader = make_client(), make_client()
    q = asker.post("/api/questions", json={"body": unique("Which box runs the sweep?")}).json()
    reader.post(f"/api/items/{q['id']}/corrections", json={"body": "It is the batch host, not the sweep host."})

    r = asker.delete(f"/api/questions/{q['id']}")
    assert r.status_code < 500, f"deleting the question returned {r.status_code}"
    if r.status_code == 200:
        assert reader.get(f"/api/items/{q['id']}").status_code == 404
