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


def test_item_relationships_endpoint(make_client):
    """Regression: this route read columns that the relationship-review schema
    change removed, so it returned 500 for any item that had a relationship."""
    a, b = make_client(), make_client()
    fact = f"The tau-{uuid.uuid4().hex[:8]} report is generated after the nightly close."
    first = a.post("/api/capture", data={"body": fact}).json()["item"]
    b.post("/api/capture", data={"body": fact})

    r = a.get(f"/api/items/{first['id']}/relationships")
    assert r.status_code == 200
    rows = r.json()
    assert rows and rows[0]["rel_type"] == "corroborates"
    assert rows[0]["state"] == "confirmed"
    assert "similar after normalization" in rows[0]["evidence"]


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
    """No self-service admin creation over HTTP; admin APIs require a session;
    a logged-in admin can add more admins."""
    anonymous = make_client()
    # The setup endpoint is gone: accounts are created with `manage.py create-admin`.
    intruder = {"username": "x_intruder", "password": "password123"}
    assert anonymous.post("/api/admin/setup", json=intruder).status_code != 200
    # ...and nothing was created by trying.
    assert anonymous.post("/api/admin/login", json=intruder).status_code == 401
    assert anonymous.get("/api/admin/concepts").status_code == 401
    assert anonymous.get("/api/admin/expertise").status_code == 401
    # Anonymous visitors are told nothing about whether admin accounts exist.
    state = anonymous.get("/api/admin/state").json()
    assert state == {"logged_in": False, "username": None}

    r = admin_client.post(
        "/api/admin/admins", json={"username": f"second{uuid.uuid4().hex[:5]}", "password": "another-pass-1"}
    )
    assert r.status_code == 200

    assert anonymous.post(
        "/api/admin/login", json={"username": "rootadmin", "password": "wrong-password"}
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

    # Below the threshold (3) nothing is suggested.
    for _ in range(2):
        author.post("/api/capture", data={"body": f"Alpha{suffix} feeds Beta{suffix} nightly. {uuid.uuid4().hex}"})
    assert _link_between(admin_client, left["id"], right["id"]) is None

    author.post("/api/capture", data={"body": f"Alpha{suffix} and Beta{suffix} share a window. {uuid.uuid4().hex}"})
    link = _link_between(admin_client, left["id"], right["id"])
    assert link["state"] == "suggested"
    assert link["occurrence_count"] == 3
    assert link["type_name"] == "related to"

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
    assert client.post("/api/admin/login", json={"username": username, "password": password}).status_code == 200
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
