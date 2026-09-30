"""Retained-score replay mechanics on isolated databases; never model inference."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


FIXTURE = Path(__file__).parent / "fixtures/local_definition_replay_recorded.json"
FIXTURE_SHA256 = "07baf982ba0ac0d556104815a2b893318963e025a520f420c138b0c47455d979"


@pytest.fixture
def replay_db(app_modules, tmp_path, monkeypatch):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    from app.db import _set_sqlite_pragma
    from app.ml import policy, runtime
    from app.models import Profile
    from ml_synthetic_records import current_synthetic_metadata

    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    recorded = json.loads(raw)["prediction"]
    assert hashlib.sha256(recorded["text"].encode()).hexdigest() == recorded["text_sha256"]
    models = {role: {"revision": revision} for role, revision in zip(
        ("extractor", "embeddings", "syntax"), recorded["metadata"]["model_version"].split(":")[:3])}
    metadata = current_synthetic_metadata(models)

    def no_models(*args, **kwargs):
        pytest.fail("The local-definition regression must not load models")

    monkeypatch.setattr(runtime.LocalModels, "__init__", no_models)
    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "local-definition.sqlite3"
    migrated = subprocess.run([sys.executable, "-m", "alembic", "-c", str(backend / "alembic.ini"),
                               "upgrade", "head"], cwd=backend, capture_output=True, text=True,
                              env={**os.environ, "MDS_DATA_DIR": str(tmp_path),
                                   "MDS_DATABASE_URL": f"sqlite:///{database}"})
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _set_sqlite_pragma)
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            db.add_all([Profile(id="definition-author"), Profile(id="dependent-author")])
            db.execute(text("UPDATE ml_state SET automation_enabled=1,pipeline_version=:version WHERE id=1"),
                       {"version": metadata[0] + ":" + policy.VERSION})
            db.commit()
            yield db, recorded, metadata
    finally:
        engine.dispose()


def _capture(db, identity, author, body):
    from app.knowledge import process_after_save
    from app.models import KnowledgeItem

    item = KnowledgeItem(id=identity, kind="note", body=body, visibility="team", author_profile_id=author)
    db.add(item)
    db.flush()
    process_after_save(db, item)
    db.commit()
    return item


def _recorded_result(recorded):
    from app.ml import eligibility, syntax
    from ml_synthetic_records import current_synthetic_result

    # The immutable record keeps its actual embedding. This application test
    # needs only the retained scores/spans and does not exercise vector search.
    result = current_synthetic_result(recorded["result"], recorded["text"])
    result["chunks"] = []
    models = {role: {"revision": revision} for role, revision in zip(
        ("extractor", "embeddings", "syntax"), recorded["metadata"]["model_version"].split(":")[:3])}
    result["eligibility"]["version"] = eligibility.version(models)
    for definition in result["corroborated_definitions"]:
        assert definition["syntax_rule_revision"] == syntax.REVISION
    return result


def _apply(db, item_id, metadata, result=None):
    from app.ml import adapter
    from app.ml.sources import snapshot

    source = snapshot(db, "item", item_id)
    if source is not None and result is None:
        cached = adapter.cached_result(db, source, metadata[0])
        assert cached is not None, "Replay must reuse the saved observation"
        result, metadata = cached
        assert result["chunks"] is None
    adapter.apply_source(db, "item", item_id, source, result, *metadata)
    db.commit()


def _settle_initial_publication(db):
    from sqlalchemy import text
    from app.ml import adapter

    # Service the initial vocabulary notification once. The tests below model
    # its bounded cached source traversal directly, without native worker leases.
    adapter.apply_vocabulary(db, "all")
    db.execute(text("DELETE FROM ml_jobs"))
    db.execute(text("UPDATE ml_state SET backfill_kind=NULL,backfill_cursor='' WHERE id=1"))
    db.commit()


def _projection(db):
    from app.ml.models import Finding

    return [(row.key, row.state, row.score, row.canonical_id) for row in db.query(Finding).filter(
        Finding.kind.in_(("concept", "alias"))).order_by(Finding.key)]


def _dependent(db, recorded, metadata):
    dependent = _capture(db, "dependent", "dependent-author", "SC")
    # Synthetic geometry for a separate source; the entity score is copied
    # unchanged from the retained SC observation, never invented or rescaled.
    span = copy.deepcopy(next(c for c in recorded["result"]["concepts"] if c["name"] == "SC"))
    span.update(start=0, end=len(dependent.body))
    result = _recorded_result(recorded)
    result.update(concepts=[span], relations=[], corroborated_definitions=[], conflict_definitions=[])
    _apply(db, dependent.id, metadata, result)
    _apply(db, dependent.id, metadata)
    return dependent


def test_recorded_local_definition_cached_replays_do_not_restart_backfill(replay_db):
    from sqlalchemy import text
    from app.ml import effective

    db, recorded, metadata = replay_db
    item = _capture(db, "definition", "definition-author", recorded["text"])
    _apply(db, item.id, metadata, _recorded_result(recorded))
    assert {term.display for term in effective.terms(db)} == {"Sidechain compression", "SC"}
    _settle_initial_publication(db)
    settled = _projection(db)
    generation = db.execute(text("SELECT backfill_generation FROM ml_state WHERE id=1")).scalar_one()
    for _ in range(3):
        _apply(db, item.id, metadata)
        assert _projection(db) == settled
        assert not db.execute(text("SELECT source_id FROM ml_jobs WHERE source_kind='vocabulary'")).all()
        state = db.execute(text("SELECT backfill_kind,backfill_generation FROM ml_state WHERE id=1")).one()
        assert tuple(state) == (None, generation)


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_alias_only_source_keeps_dependency_when_local_definition_is_removed(replay_db, mutation):
    from app.concepts import source_concepts
    from app.knowledge import delete_item, process_after_save
    from app.ml import adapter, effective
    from app.ml.models import Evidence, Finding
    from app.ml.sources import snapshot

    db, recorded, metadata = replay_db
    definition = _capture(db, "definition", "definition-author", recorded["text"])
    _apply(db, definition.id, metadata, _recorded_result(recorded))
    concept = next(c for c in effective.concepts(db) if c.name == "Sidechain compression")
    dependent = _dependent(db, recorded, metadata)
    evidence = db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key).filter(
        Evidence.source_id == dependent.id, Finding.kind == "concept").all()
    assert evidence
    assert all(json.loads(row.features).get("term_routes") for row in evidence)
    assert {c.id for c in source_concepts(db, "item", dependent.id, dependent.body)} == {concept.id}

    if mutation == "edit":
        definition.body = "The audio notebook has been retired."
        db.commit()
        process_after_save(db, definition)
        db.commit()
        assert adapter.cached_result(db, snapshot(db, "item", definition.id), metadata[0]) is None
        empty = _recorded_result(recorded)
        empty.update(concepts=[], relations=[], corroborated_definitions=[], conflict_definitions=[])
    else:
        delete_item(db, definition)
        empty = None
    assert "SC" not in {term.display for term in effective.terms(db)}
    assert concept.id not in {c.id for c in source_concepts(db, "item", dependent.id, dependent.body)}
    _apply(db, definition.id, metadata, empty)
    for _ in range(2):
        _apply(db, dependent.id, metadata)
        assert not any(term.display == "SC" and term.concept_id == concept.id for term in effective.terms(db))
        assert concept.id not in {c.id for c in source_concepts(db, "item", dependent.id, dependent.body)}


def test_suppressed_alias_blocks_dependents_but_not_its_current_local_definition(replay_db):
    from app.concepts import source_concepts
    from app.ml import effective
    from app.ml.api import Decision, decide
    from app.ml.models import Finding
    from app.models import Account

    db, recorded, metadata = replay_db
    definition = _capture(db, "definition", "definition-author", recorded["text"])
    _apply(db, definition.id, metadata, _recorded_result(recorded))
    concept = next(c for c in effective.concepts(db) if c.name == "Sidechain compression")
    dependent = _dependent(db, recorded, metadata)
    alias = db.query(Finding).filter_by(kind="alias").one()
    # Call the real decision handler with this isolated session. Authentication
    # is covered by API tests; no global application database is changed here.
    decide(alias.key, Decision(mode="suppressed"), Account(username="regression-admin"), db)
    for replay in (False, True):
        if replay:
            _apply(db, definition.id, metadata)
            _apply(db, dependent.id, metadata)
        assert "SC" not in {term.display for term in effective.terms(db)}
        assert concept.id in {c.id for c in effective.concepts(db)}
        assert concept.id in {c.id for c in source_concepts(db, "item", definition.id, definition.body)}
        assert concept.id not in {c.id for c in source_concepts(db, "item", dependent.id, dependent.body)}
        assert db.get(Finding, alias.key).state == "suppressed"


def test_v7_local_definition_upgrade_replays_cached_self_routes_once(replay_db, tmp_path, monkeypatch):
    import sqlite3
    import threading
    from sqlalchemy import text
    from app.ml import adapter, effective, identity, policy, queue, worker
    from app.ml.models import Evidence, Finding, Source
    from app.ml.sources import snapshot

    if not queue.sqlite_is_safe(sqlite3.sqlite_version_info):
        pytest.skip("Real worker upgrade requires the supported SQLite WAL-reset fix")
    pytest.importorskip("psutil")
    assert policy.VERSION != "grounded-cold-start-v7", "Selection repair must schedule policy-only backfill"
    db, recorded, metadata = replay_db
    adapter.bootstrap(db)
    item = _capture(db, "definition", "definition-author", recorded["text"])
    _apply(db, item.id, metadata, _recorded_result(recorded))
    concept = next(c for c in effective.concepts(db) if c.name == "Sidechain compression")
    _settle_initial_publication(db)
    expected_projection = _projection(db)
    source = snapshot(db, "item", item.id)
    route = identity.term_route(db, "SC", concept.id)
    assert route is not None
    short = next(c for c in recorded["result"]["concepts"] if c["name"] == "SC")
    full = max((c for c in recorded["result"]["concepts"] if c["name"] == concept.name),
               key=lambda c: c["score"])
    targets = db.query(Finding).filter(Finding.kind.in_(("concept", "mention")),
                                      Finding.canonical_id == concept.id).all()
    assert {row.kind for row in targets} == {"concept", "mention"}
    # Reconstruct the exact v7 committed selection seen in the retained case14
    # snapshot: strong SC has a self-route; weaker literal full-name evidence
    # is separate. All scores/offsets come unchanged from the recorded output.
    for finding in targets:
        db.query(Evidence).filter_by(finding_key=finding.key, source_id=item.id).delete(
            synchronize_session="fetch")
        for span, dependencies in ((short, {"term_routes": [route]}), (full, {})):
            adapter._evidence(db, finding, source, span["start"], span["end"], span["score"], metadata[0],
                              grounded=True, **({"label": span["label"]} if finding.kind == "concept" else {}),
                              **dependencies)
        finding.policy_version = "grounded-cold-start-v7"
    db.execute(text("UPDATE ml_state SET pipeline_version=:version,decision_policy='{}' WHERE id=1"),
               {"version": metadata[0] + ":grounded-cold-start-v7"})
    db.commit()
    raw_cache = db.get(Source, ("item", item.id)).result
    generation = db.execute(text("SELECT backfill_generation FROM ml_state WHERE id=1")).scalar_one()
    assert db.execute(text("SELECT count(*) FROM ml_jobs")).scalar_one() == 0
    assert sum(bool(json.loads(row.features).get("term_routes")) for row in
               db.query(Evidence).filter_by(source_id=item.id)) == 2
    db.commit()

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "models.json").write_text(json.dumps({"models": {
        role: {"revision": revision} for role, revision in zip(
            ("extractor", "embeddings", "syntax"), metadata[0].split(":")[:3])}}))

    def no_inference(*args, **kwargs):
        pytest.fail("Policy-only upgrade must reuse the complete current model cache")

    claims = []
    process_claim = worker.Supervisor.process_claim

    def bounded_claim(supervisor):
        claims.append((supervisor.claim.source_kind, supervisor.claim.source_id))
        assert len(claims) <= 8, "Cached upgrade must not cycle vocabulary/source work"
        return process_claim(supervisor)

    monkeypatch.setattr(worker.InferenceProcess, "analyze", no_inference)
    monkeypatch.setattr(worker.Supervisor, "process_claim", bounded_claim)
    monkeypatch.setattr(worker, "lower_priority", lambda: None)
    path = Path(db.get_bind().url.database)
    for restart in range(2):
        supervisor = worker.Supervisor(path, assets, threading.Event())
        try:
            assert supervisor.run("drain") == 0
        finally:
            supervisor.close()
        db.expire_all()
        assert _projection(db) == expected_projection
        assert db.get(Source, ("item", item.id)).result == raw_cache
        assert all(not json.loads(row.features).get("term_routes") for row in
                   db.query(Evidence).filter_by(source_id=item.id))
        assert all(db.get(Finding, row.key).policy_version == policy.VERSION for row in targets)
        state = db.execute(text("SELECT pipeline_version,backfill_kind,backfill_generation FROM ml_state WHERE id=1")).one()
        assert tuple(state) == (metadata[0] + ":" + policy.VERSION, None, generation + 1)
        assert db.execute(text("SELECT count(*) FROM ml_jobs")).scalar_one() == 0
        if restart == 0:
            assert ("item", item.id) in claims
            first_claims = list(claims)
        else:
            assert claims == first_claims
        db.commit()
