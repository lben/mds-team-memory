"""API-level proof of the PRD 'meaningful automated proof' workflows 1-10."""

import io
import uuid


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

    queue = expert.get("/api/questions", params={"mine_expertise": True}).json()
    assert any(q["id"] == question["id"] for q in queue)
    notes = expert.get("/api/notifications").json()
    assert any(n["item_id"] == question["id"] for n in notes["notifications"])

    preview = admin_client.get(
        "/api/admin/routing-preview", params={"q": f"oly-{suffix} is failing"}
    ).json()
    assert f"Olymp{suffix}" in preview["detected"]


def test_admin_auth_gates(make_client, admin_client):
    """Admin onboarding happens once; admin APIs require a session; more admins can be added."""
    anonymous = make_client()
    assert anonymous.post(
        "/api/admin/setup", json={"username": "x_intruder", "password": "password123"}
    ).status_code == 403
    assert anonymous.get("/api/admin/concepts").status_code == 401
    assert anonymous.get("/api/admin/expertise").status_code == 401

    r = admin_client.post(
        "/api/admin/admins", json={"username": f"second{uuid.uuid4().hex[:5]}", "password": "another-pass-1"}
    )
    assert r.status_code == 200

    assert anonymous.post(
        "/api/admin/login", json={"username": "rootadmin", "password": "wrong-password"}
    ).status_code == 401


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
