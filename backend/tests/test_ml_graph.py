"""Public graph behavior with explicitly synthetic persisted claims and edits."""

import json
import uuid

import pytest


@pytest.fixture
def automated_graph(admin_client):
    from sqlalchemy import text
    from app.db import SessionLocal

    with SessionLocal() as db:
        previous = db.execute(text("SELECT automation_enabled FROM ml_state WHERE id=1")).scalar()
        db.execute(text("UPDATE ml_state SET automation_enabled=1 WHERE id=1"))
        db.commit()
    names = [f"{name}{uuid.uuid4().hex[:8]}" for name in ("Orion", "Ledger", "Harbor")]
    concepts = [admin_client.post("/api/admin/concepts", json={"name": name}).json() for name in names]
    yield concepts
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET automation_enabled=:previous WHERE id=1"), {"previous": previous})
        db.commit()


def seed_claim(src, dst, items, *, kind="relationship", predicate="feeds", link_id=None, score=0.8):
    from ml_synthetic_records import current_synthetic_metadata, select_synthetic_pipeline
    from app.db import SessionLocal
    from app.models import RELATED_TO_ID, Relationship, utcnow
    from app.ml.models import Evidence, Finding, Source
    from app.ml import identity, relation_syntax, syntax
    from app.ml.policy import VERSION
    from app.ml.sources import finding_key, snapshot

    with SessionLocal() as db:
        metadata = current_synthetic_metadata()
        select_synthetic_pipeline(db, metadata)
        link = db.get(Relationship, link_id) if link_id else Relationship(
            src_kind="concept", src_id=src["id"], dst_kind="concept", dst_id=dst["id"],
            relationship_type_id=RELATED_TO_ID, state="suggested")
        if not link_id:
            db.add(link)
            db.flush()
        key = finding_key(kind, src["id"], predicate, dst["id"])
        db.add(Finding(key=key, kind=kind, payload=json.dumps({"src_id": src["id"], "dst_id": dst["id"],
                      "predicate": predicate}), state="active", score=score, calibrated=False,
                      policy_version=VERSION, canonical_id=link.id, created_at=utcnow(), updated_at=utcnow()))
        db.flush()
        for item in items:
            source = snapshot(db, "item", item["id"])
            db.merge(Source(kind="item", id=source.id, content_hash=source.content_hash, valid=True,
                            model_version=metadata[0], updated_at=utcnow(),
                            result=json.dumps({"relation_guard_revision": relation_syntax.REVISION,
                                               "alias_scope_contract": identity.scope_contract(),
                                               "definitions_indexed": 2,
                                               "conflict_coverage_revision": syntax.CONFLICT_REVISION,
                                               "conflict_definitions": []})))
            db.add(Evidence(key=finding_key("evidence", key, source.id), finding_key=key,
                            source_kind="item", source_id=source.id, source_hash=source.content_hash,
                            group_key=source.group_key, author_id=source.author_id, start=0, end=len(source.text),
                            raw_score=score, polarity="positive", model_version=metadata[0],
                            features=json.dumps({"text_hash": source.text_hash, "literal_support": True,
                                                 "relation_guard_revision": relation_syntax.REVISION,
                                                 "assertion_allowed": True, "locator": "", "origin": "note"})))
        db.commit()
        return link.id


def capture(client, body):
    result = client.post("/api/capture", data={"body": body})
    assert result.status_code == 200
    return result.json()["item"]


@pytest.mark.parametrize("polarity", ["positive", "negative"])
@pytest.mark.parametrize("missing_support", ["literal_support", "assertion_allowed"])
def test_unqualified_records_are_context_not_counted_support_or_conflict(
        automated_graph, make_client, polarity, missing_support):
    from app.db import SessionLocal
    from app.ml.models import Evidence

    left, right, _ = automated_graph
    owners = [make_client() for _ in range(3)]
    items = [capture(owner, body) for owner, body in zip(owners, (
        f"{left['name']} feeds {right['name']} through the morning import pipeline.",
        f"The independent warehouse deployment transfers records from {left['name']} to {right['name']}.",
        f"A pending question mentions {left['name']} beside {right['name']}; no factual support is supplied."))]
    link_id = seed_claim(left, right, items)
    with SessionLocal() as db:
        row = db.query(Evidence).filter_by(source_kind="item", source_id=items[-1]["id"]).one()
        row.polarity = polarity
        row.features = json.dumps({**json.loads(row.features), missing_support: False})
        db.commit()
    detail = owners[0].get(f"/api/graph/links/{link_id}/evidence").json()
    claim = next(row for row in detail["claims"] if row["predicate"] == "feeds")
    assert claim["state"] == "active" and claim["support_count"] == 2 and not claim["conflicts"]
    assert detail["summary"]["support_count"] == 2
    context = next(row for row in claim["sources"] if row["source_id"] == items[-1]["id"])
    assert context["polarity"] == polarity and context[missing_support] is False


def test_graph_live_claims_direction_weak_toggle_and_source_retraction(automated_graph, make_client):
    left, right, weak = automated_graph
    first, second, reader = make_client(), make_client(), make_client()
    one = capture(first, f"{left['name']} feeds {right['name']} through the morning import pipeline.")
    two = capture(second, f"The warehouse receives {left['name']} records: {right['name']} is its destination.")
    link_id = seed_claim(left, right, [one, two])
    seed_claim(right, left, [two], predicate="depends_on", link_id=link_id, score=0.68)
    weak_id = seed_claim(left, weak, [one], kind="association", predicate="related_to")

    graph = reader.get("/api/graph/global").json()
    edge = next(edge for edge in graph["edges"] if edge["link_id"] == link_id)
    assert (edge["state"], edge["style"], edge["label"], edge["directed"]) == ("active", "solid", "feeds", True)
    assert not any(edge["link_id"] == weak_id for edge in graph["edges"])
    shown = reader.get("/api/graph/global?show_weak=true").json()
    association = next(edge for edge in shown["edges"] if edge["link_id"] == weak_id)
    assert (association["state"], association["style"], association["directed"]) == ("weak", "dotted", False)
    clusters = [{concept["id"] for concept in cluster["concepts"]} for cluster in shown["clusters"]]
    assert any({left["id"], right["id"]} <= cluster and weak["id"] not in cluster for cluster in clusters)
    local = reader.get("/api/graph/local", params={"concept_id": right["id"]}).json()
    arrow = next(edge for edge in local["edges"] if edge["link_id"] == link_id)
    assert (arrow["source"], arrow["target"]) == (f"c:{left['id']}", f"c:{right['id']}")

    detail = reader.get(f"/api/graph/links/{link_id}/evidence").json()
    assert len(detail["claims"]) == 2
    for claim in detail["claims"]:
        assert claim["policy_version"] and "score" not in claim
        for source in claim["sources"]:
            assert source["quote"] == source["text"][source["start"]:source["end"]]
            assert source["source_hash"] and source["model_version"]

    assert first.delete(f"/api/items/{one['id']}").status_code == 200
    edge = next(edge for edge in reader.get("/api/graph/global").json()["edges"] if edge["link_id"] == link_id)
    assert (edge["state"], edge["style"]) == ("held", "dashed")
    assert second.put(f"/api/items/{two['id']}", json={"body": "The old pipeline account was removed."}).status_code == 200
    assert not any(edge["link_id"] == link_id for edge in reader.get("/api/graph/global").json()["edges"])
    detail = reader.get(f"/api/graph/links/{link_id}/evidence").json()
    assert all(not claim["sources"] for claim in detail["claims"])


def test_graph_manual_label_pin_reject_restore_and_delete(automated_graph, make_client, admin_client):
    left, right, _ = automated_graph
    author, reader = make_client(), make_client()
    item = capture(author, f"{left['name']} feeds {right['name']} during settlement.")
    link_id = seed_claim(left, right, [item])
    custom = admin_client.post("/api/admin/relationship-types", json={"name": f"owns-{uuid.uuid4().hex[:8]}"}).json()
    assert admin_client.patch(f"/api/graph/links/{link_id}", json={"type_id": custom["id"], "note": "Team decision"}).status_code == 200
    assert author.delete(f"/api/items/{item['id']}").status_code == 200
    edge = next(edge for edge in reader.get("/api/graph/global").json()["edges"] if edge["link_id"] == link_id)
    assert (edge["origin"], edge["state"], edge["label"]) == ("manual", "active", custom["name"])
    assert edge["support_count"] == 0
    assert admin_client.patch(f"/api/graph/links/{link_id}", json={"state": "rejected"}).status_code == 200
    assert not any(edge["link_id"] == link_id for edge in reader.get("/api/graph/global").json()["edges"])
    assert admin_client.patch(f"/api/graph/links/{link_id}", json={"state": "suggested"}).status_code == 200
    # Explicit restoration cannot make missing evidence active.
    assert not any(edge["link_id"] == link_id for edge in reader.get("/api/graph/global").json()["edges"])
    assert admin_client.patch(f"/api/graph/links/{link_id}", json={"state": "confirmed"}).status_code == 200
    assert admin_client.delete(f"/api/graph/links/{link_id}").status_code == 200
    capture(author, f"{left['name']} and {right['name']} are mentioned again after deletion.")
    links = admin_client.get("/api/graph/links", params={"concept_id": left["id"]}).json()
    assert not any(right["id"] in (link["src_id"], link["dst_id"]) for link in links)


def test_overview_bound_and_focus_reaches_omitted_concepts(automated_graph, make_client):
    from app.db import SessionLocal
    from app.models import Concept, ConceptTerm

    with SessionLocal() as db:
        for _ in range(205):
            concept = Concept()
            db.add(concept)
            db.flush()
            name = f"Bounded-{uuid.uuid4().hex}"
            db.add(ConceptTerm(concept_id=concept.id, term=name.lower(), display=name, is_canonical=True))
        db.commit()
    reader = make_client()
    graph = reader.get("/api/graph/global").json()
    shown = {concept["id"] for cluster in graph["clusters"] for concept in cluster["concepts"]}
    assert len(shown) == 200 and len(graph["edges"]) <= 400
    assert graph["omitted_nodes"] == graph["total_nodes"] - 200 > 0
    omitted = next(concept for concept in reader.get("/api/graph/concepts").json() if concept["id"] not in shown)
    local = reader.get("/api/graph/local", params={"concept_id": omitted["id"]}).json()
    assert local["nodes"][0]["id"] == f"c:{omitted['id']}"
    assert len(local["nodes"]) <= 13


def test_overview_sources_link_to_shown_concepts_and_are_bounded(admin_client, make_client, monkeypatch):
    # The overview's concept bound depends on everything else in the shared
    # database, so this states which concepts are shown instead.
    from app.db import SessionLocal
    from app.routers import graph

    shown, hidden = (admin_client.post("/api/admin/concepts", json={"name": f"{name}{uuid.uuid4().hex[:8]}"}).json()
                     for name in ("Beacon", "Lantern"))
    writer = make_client()
    assert writer.post("/api/capture", data={"body": f"{shown['name']} restarts nightly."}).status_code == 200
    assert writer.post("/api/questions", json={"body": f"Who owns {shown['name']}?"}).status_code == 200
    for filename, name in (("shown.txt", shown["name"]), ("hidden.txt", hidden["name"])):
        assert writer.post("/api/documents", files={"file": (filename, f"{name} runbook.".encode(), "text/plain")}).status_code == 200

    with SessionLocal() as db:
        nodes, edges, omitted = graph._overview_sources(db, graph._team_subject_concepts(db), {shown["id"]})
        assert sorted((node["type"], node["type"] == "document" and node["label"]) for node in nodes) == [
            ("document", "shown.txt"), ("item", False), ("question", False)]  # not hidden.txt
        assert {node["id"] for node in nodes} == {edge["source"] for edge in edges}  # nothing floats unconnected
        assert omitted == 0

        monkeypatch.setattr(graph, "MAX_OVERVIEW_SOURCES", 1)
        nodes, edges, omitted = graph._overview_sources(db, graph._team_subject_concepts(db), {shown["id"]})
        assert len(nodes) == 1 and omitted == 2
