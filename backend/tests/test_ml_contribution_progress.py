"""An author's new contribution reports real processing progress, then what it changed."""

import json
import time
import uuid

import pytest


@pytest.fixture
def live_worker(app_modules):
    """Automation on and a worker lease held, as while the worker runs; restored afterwards."""
    from sqlalchemy import text
    from app import config
    from app.db import SessionLocal
    from app.ml.activity import activity_path, lease_id
    from app.ml.worker import database_path

    token = uuid.uuid4().hex
    with SessionLocal() as db:
        previous = db.execute(text("SELECT automation_enabled, worker_token, worker_lease_until FROM ml_state")).one()
        db.execute(text("UPDATE ml_state SET automation_enabled=1, worker_token=:t, worker_lease_until=:u"),
                   {"t": token, "u": time.time() + 600})
        db.commit()
    path = activity_path(database_path(config.DATABASE_URL))

    def report(job, recent=()):
        path.write_text(json.dumps({"pid": 1, "started_at": 0, "lease": lease_id(token), "activity": None,
                                    "job": job, "recent": list(recent)}))
    yield report
    path.unlink(missing_ok=True)
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET automation_enabled=:a, worker_token=:t, worker_lease_until=:u"),
                   dict(zip("atu", previous)))
        db.commit()


def finish(item_id):
    from sqlalchemy import text
    from app.db import SessionLocal

    with SessionLocal() as db:
        db.execute(text("DELETE FROM ml_jobs WHERE source_kind='item' AND source_id=:id"), {"id": item_id})
        db.commit()


def seed(kind, payload, items, *, state, score, features, canonical_id=None):
    """A decided finding with evidence from each item, as the worker records it."""
    from ml_synthetic_records import current_synthetic_metadata, select_synthetic_pipeline
    from app.db import SessionLocal
    from app.models import utcnow
    from app.ml import identity, relation_syntax, syntax
    from app.ml.models import Evidence, Finding, Source
    from app.ml.policy import VERSION
    from app.ml.sources import finding_key, snapshot

    with SessionLocal() as db:
        metadata = current_synthetic_metadata()
        select_synthetic_pipeline(db, metadata)
        key = finding_key(kind, *payload.values())
        db.add(Finding(key=key, kind=kind, payload=json.dumps(payload), state=state, score=score, calibrated=False,
                       policy_version=VERSION, canonical_id=canonical_id, created_at=utcnow(), updated_at=utcnow()))
        db.flush()
        for item_id in items:
            source = snapshot(db, "item", item_id)
            db.merge(Source(kind="item", id=item_id, content_hash=source.content_hash, valid=True,
                            model_version=metadata[0], updated_at=utcnow(),
                            result=json.dumps({"relation_guard_revision": relation_syntax.REVISION,
                                               "alias_scope_contract": identity.scope_contract(),
                                               "definitions_indexed": 2,
                                               "conflict_coverage_revision": syntax.CONFLICT_REVISION,
                                               "conflict_definitions": []})))
            db.add(Evidence(key=finding_key("evidence", key, item_id), finding_key=key, source_kind="item",
                            source_id=item_id, source_hash=source.content_hash, group_key=source.group_key,
                            author_id=source.author_id, start=0, end=len(source.text), raw_score=score,
                            polarity="positive", model_version=metadata[0],
                            features=json.dumps({"text_hash": source.text_hash, "origin": "note", **features})))
        db.commit()


def progress(client, *ids):
    response = client.get("/api/ml/contributions", params={"ids": list(ids)})
    assert response.status_code == 200
    return response.json()


CONCEPT = {"grounded": True, "eligibility_margin": 0.9, "label": "named entity"}


def test_progress_follows_the_worker_then_reports_what_the_post_changed(live_worker, make_client, admin_client):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import relation_syntax

    suffix = uuid.uuid4().hex[:6]
    left, right = (admin_client.post("/api/admin/concepts", json={"name": f"{name}{suffix}"}).json()
                   for name in ("Kestrel", "Marlin"))
    author, other, reader = make_client(), make_client(), make_client()
    body = f"Halcyon{suffix} sends {left['name']} figures to {right['name']} every night."
    item = author.post("/api/capture", data={"body": body}).json()["item"]["id"]
    earlier = other.post("/api/capture", data={"body": f"{left['name']} feeds {right['name']} nightly."}).json()["item"]["id"]

    # Queued, then the worker's real steps, in order; only the author is told.
    assert progress(author, item)[item] == {"state": "working", "phase": "ingesting", "percent": 3}
    assert progress(reader, item) == {}
    seen = []
    for stage, note in [("reading the source", None), ("running inference", {"step": "loading models"}),
                        ("running inference", {"step": "parsing sentences", "window": 1, "windows": 2}),
                        ("running inference", {"step": "extracting relations", "window": 1, "windows": 2}),
                        ("running inference", {"step": "extracting entities", "window": 2, "windows": 2}),
                        ("running inference", {"step": "judging concept relevance"}), ("saving results", None)]:
        live_worker({"kind": "item", "id": item, "claimed_at": 0, "stage": stage, "stage_since": 0, "progress": note})
        with SessionLocal() as db:  # the worker holds the job's lease while it works
            db.execute(text("UPDATE ml_jobs SET lease_token='w', lease_until=:u WHERE source_id=:id"),
                       {"u": time.time() + 60, "id": item})
            db.commit()
        state = progress(author, item)[item]
        seen.append((state["phase"], state["percent"]))
    assert [phase for phase, _ in seen] == ["ingesting", "ingesting", "categorizing", "relating", "categorizing",
                                            "connecting", "connecting"]
    assert [percent for _, percent in seen] == sorted(percent for _, percent in seen)

    finish(item)
    assert progress(author, item)[item]["state"] == "done"


def concept_made_by_worker(name):
    from app.db import SessionLocal
    from app.models import Concept, ConceptTerm

    with SessionLocal() as db:
        concept = Concept()
        db.add(concept)
        db.flush()
        db.add(ConceptTerm(concept_id=concept.id, term=name.lower(), display=name, is_canonical=True))
        db.commit()
        return concept.id


def test_done_reports_only_what_the_post_changed_for_the_team(live_worker, make_client, admin_client):
    from app.db import SessionLocal
    from app.ml import relation_syntax
    from app.models import Relationship, RelationshipType

    s = uuid.uuid4().hex[:6]
    kestrel, marlin, osprey = (admin_client.post("/api/admin/concepts", json={"name": f"{name}{s}"}).json()
                               for name in ("Kestrel", "Marlin", "Osprey"))
    author, other = make_client(), make_client()
    item = author.post("/api/capture", data={"body": f"Halcyon{s} and Wren{s} move {kestrel['name']} data to "
                                                     f"{marlin['name']}; Plover{s} watches {osprey['name']}."}
                       ).json()["item"]["id"]
    earlier = other.post("/api/capture", data={"body": f"Wren{s}: {kestrel['name']} feeds {marlin['name']}."}
                         ).json()["item"]["id"]
    finish(item)
    finish(earlier)
    halcyon, wren = concept_made_by_worker(f"Halcyon{s}"), concept_made_by_worker(f"Wren{s}")
    live_worker(None, recent=[{"kind": "item", "id": item, "outcome": "done", "created": [halcyon, wren]}])
    relation = {"literal_support": True, "assertion_allowed": True, "locator": "",
                "relation_guard_revision": relation_syntax.REVISION}
    # A concept this post's job created, one it completed with an earlier post, an existing
    # hand-made concept the model also found, and a name one more post would make a concept.
    seed("concept", {"name": f"Halcyon{s}"}, [item], state="active", score=0.999, features=CONCEPT, canonical_id=halcyon)
    seed("concept", {"name": f"Wren{s}"}, [earlier, item], state="active", score=0.9, features=CONCEPT, canonical_id=wren)
    seed("concept", {"name": kestrel["name"]}, [item], state="active", score=0.999, features=CONCEPT,
         canonical_id=kestrel["id"])
    seed("concept", {"name": f"Plover{s}"}, [item], state="held", score=0.9, features=CONCEPT)
    # A link this post confirmed, an active claim the team does not see as a link, and
    # a claim one more post would confirm.
    seed("relationship", {"src_id": kestrel["id"], "predicate": "feeds", "dst_id": marlin["id"]}, [earlier, item],
         state="active", score=0.8, features=relation)
    seed("relationship", {"src_id": marlin["id"], "predicate": "uses", "dst_id": osprey["id"]}, [earlier, item],
         state="active", score=0.8, features=relation)
    seed("relationship", {"src_id": osprey["id"], "predicate": "depends_on", "dst_id": kestrel["id"]}, [item],
         state="held", score=0.8, features=relation)
    with SessionLocal() as db:
        feeds = db.query(RelationshipType).filter_by(name="feeds").first() or RelationshipType(name="feeds")
        db.add(feeds)
        db.flush()
        db.add(Relationship(src_kind="concept", src_id=kestrel["id"], dst_kind="concept", dst_id=marlin["id"],
                            relationship_type_id=feeds.id, state="confirmed"))
        db.commit()

    done = progress(author, item)[item]
    assert done["outcomes"] == [
        {"kind": "new_concept", "name": f"Halcyon{s}"},
        {"kind": "confirmed_concept", "name": f"Wren{s}"},
        {"kind": "confirmed_connection", "name": f"{kestrel['name']} feeds {marlin['name']}"},
        {"kind": "tagged", "names": sorted([kestrel["name"], marlin["name"], osprey["name"]])},
        {"kind": "noted_concept", "name": f"Plover{s}"},
        {"kind": "noted_connection", "name": f"{osprey['name']} depends on {kestrel['name']}"}]


def test_private_posts_and_other_people_get_nothing(live_worker, make_client):
    author, reader = make_client(), make_client()
    item = author.post("/api/capture", data={"body": f"Team note {uuid.uuid4().hex}"}).json()["item"]["id"]
    assert progress(reader, item) == {}
    from sqlalchemy import text
    from app.db import SessionLocal
    with SessionLocal() as db:
        db.execute(text("UPDATE knowledge_items SET visibility='private' WHERE id=:id"), {"id": item})
        db.commit()
    assert progress(author, item) == {}


def test_nothing_is_shown_while_automation_is_off(live_worker, make_client):
    from sqlalchemy import text
    from app.db import SessionLocal

    author = make_client()
    item = author.post("/api/capture", data={"body": f"Plain note {uuid.uuid4().hex}"}).json()["item"]["id"]
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET automation_enabled=0"))
        db.commit()
    assert progress(author, item)[item] == {"state": "off"}
    with SessionLocal() as db:  # automation on, but no worker holding the lease
        db.execute(text("UPDATE ml_state SET automation_enabled=1, worker_lease_until=:t"), {"t": time.time() - 1})
        db.commit()
    assert progress(author, item)[item] == {"state": "off"}
