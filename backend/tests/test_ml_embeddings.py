"""Real migrated SQLite storage, generation, quota, and bounded retrieval paths."""
import json
from pathlib import Path
import sqlite3
import struct
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.ml import embeddings, policy, runtime
from app.ml.sources import digest, snapshot
from test_ml_queue import queue_db, queue_template

VECTOR_BYTES = embeddings.ROW_OVERHEAD + 12

@pytest.fixture
def store(app_modules, queue_db, monkeypatch):
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: 20 * 1024**3)
    engine = create_engine(f"sqlite:///{queue_db}")
    with Session(engine) as db:
        yield db
    engine.dispose()


def add_source(db, source_id, body="Settlement ledger receives ledger records.", visibility="team"):
    db.execute(text("""INSERT INTO knowledge_items
      (id,kind,body,visibility,author_profile_id,created_at,updated_at)
      VALUES (:id,'note',:body,:visibility,'author',datetime('now'),datetime('now'))"""),
               {"id": source_id, "body": body, "visibility": visibility})
    return snapshot(db, "item", source_id)


def vector(values=(1.0, 0.0, 0.0)):
    return struct.pack(f"<{len(values)}f", *values)


def chunks(source, values=(1.0, 0.0, 0.0)):
    return [{"start": 0, "end": len(source.text), "vector": vector(values)}] if source.text else []


def save(db, source, generation="old", values=(1.0, 0.0, 0.0), *, max_bytes=embeddings.MAX_BYTES, reuse=False):
    version = runtime.inference_version({"extractor": {"revision": "extractor"}, "embeddings": {"revision": generation}})
    db.execute(text("UPDATE ml_state SET pipeline_version=:version WHERE id=1"),
               {"version": version + ":" + policy.VERSION})
    result = None if reuse else chunks(source, values)
    embeddings.replace_source(db, source, result, generation, len(values), max_bytes=max_bytes)
    db.execute(text("""INSERT INTO ml_sources(kind,id,content_hash,valid,model_version,result,updated_at)
      VALUES(:kind,:id,:hash,1,:version,:result,datetime('now'))
      ON CONFLICT(kind,id) DO UPDATE SET content_hash=excluded.content_hash,valid=1,
      model_version=excluded.model_version,result=excluded.result"""),
        {"kind": source.kind, "id": source.id, "hash": source.content_hash, "version": version,
         "result": json.dumps({"text_hash": digest(source.text), "embedding_version": generation,
                               "dimensions": len(values), "embedding_count": len(chunks(source, values))})})


def idle(db):
    db.execute(text("DELETE FROM ml_jobs"))
    db.execute(text("UPDATE ml_state SET backfill_kind=NULL WHERE id=1"))


def budget(db):
    return db.execute(text("SELECT * FROM ml_budget WHERE id=1")).mappings().one()


def finish(db):
    for _ in range(30):
        if not embeddings.finish_generation(db):
            return
    pytest.fail("Bounded housekeeping did not finish")


def test_worker_creator_engine_checks_database_volume_before_allocation(app_modules, queue_db, monkeypatch):
    checked = []

    def disk_usage(path):
        checked.append(path)
        return SimpleNamespace(free=embeddings.FREE_RESERVE - 1 if path == queue_db.parent else 20 * 1024**3)

    monkeypatch.setattr(embeddings.shutil, "disk_usage", disk_usage)
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(queue_db))
    try:
        with Session(engine) as db:
            source = add_source(db, "source")
            with pytest.raises(embeddings.StoragePressure, match="filesystem reserve"):
                save(db, source)
            assert checked == [Path(queue_db).resolve().parent]
            assert budget(db)["embedding_bytes"] == 0
    finally:
        engine.dispose()


def test_replacement_reuse_private_edit_and_deletion(store):
    source = add_source(store, "source")
    save(store, source)
    initial_bytes = budget(store)["embedding_bytes"]
    assert embeddings.nearest(store, vector(), "old")[0]["source_id"] == "source"
    store.execute(text("UPDATE knowledge_items SET kind='question' WHERE id='source'"))
    changed = snapshot(store, "item", source.id)
    assert not embeddings.nearest(store, vector(), "old")
    save(store, changed, reuse=True)
    assert budget(store)["embedding_bytes"] == initial_bytes
    assert embeddings.nearest(store, vector(), "old")[0]["end"] == len(source.text)
    store.execute(text("UPDATE knowledge_items SET body='Changed source words.' WHERE id='source'"))
    with pytest.raises(ValueError, match="Cached embeddings"):
        save(store, snapshot(store, "item", source.id), reuse=True)
    store.execute(text("UPDATE knowledge_items SET visibility='private' WHERE id='source'"))
    assert not embeddings.nearest(store, vector(), "old")
    with pytest.raises(ValueError, match="team-visible"):
        save(store, changed)
    embeddings.invalidate_source(store, "item", "source")
    assert budget(store)["embedding_bytes"] == 0


@pytest.mark.parametrize("change", ["schema", "code", "extractor"])
def test_extraction_upgrade_invalidates_cache_without_changing_embedding_generation(store, monkeypatch, change):
    from app.ml import adapter, runtime

    models = {"extractor": {"revision": "extractor"}, "embeddings": {"revision": "old"}}
    version = runtime.inference_version(models)
    source = add_source(store, "source")
    save(store, source)
    result = json.loads(store.execute(text("SELECT result FROM ml_sources WHERE id='source'")).scalar_one())
    result.update(concepts=[], relations=[])
    store.execute(text("UPDATE ml_sources SET model_version=:version,result=:result WHERE id='source'"),
                  {"version": version, "result": json.dumps(result)})
    cached, metadata = adapter.cached_result(store, source, version)
    assert cached["chunks"] is None and metadata[1] == "old"
    vectors = store.execute(text("SELECT generation,vector FROM ml_embeddings")).all()

    if change == "schema":
        monkeypatch.setitem(runtime.ENTITIES, "named entity", "An updated entity definition.")
    elif change == "code":
        monkeypatch.setattr(runtime, "EXTRACTION_VERSION", "next-extraction-version")
    else:
        models["extractor"]["revision"] = "next-extractor"
    assert adapter.cached_result(store, source, runtime.inference_version(models)) is None
    assert store.execute(text("SELECT generation,vector FROM ml_embeddings")).all() == vectors
    assert models["embeddings"]["revision"] == metadata[1]


def test_rebuild_keeps_old_generation_until_every_source_is_current(store):
    one, two = add_source(store, "one"), add_source(store, "two")
    empty = add_source(store, "empty", body="")
    store.execute(text("""INSERT INTO document_passages(id,document_id,ord,text,locator)
      VALUES('passage','doc',0,'Document text.','Page 1')"""))
    passage = snapshot(store, "passage", "passage")
    for source in (one, two, empty, passage):
        save(store, source)
    old_bytes = budget(store)["embedding_bytes"]
    save(store, one, "new", (0.0, 1.0, 0.0, 0.0))
    assert budget(store)["active_generation"] == "old"
    assert budget(store)["staging_reserved_bytes"] >= 3 * (16 + embeddings.ROW_OVERHEAD)
    assert budget(store)["embedding_bytes"] == old_bytes + VECTOR_BYTES + 4
    assert embeddings.nearest(store, vector(), "old")
    assert not embeddings.nearest(store, vector((0.0, 1.0, 0.0, 0.0)), "new")
    idle(store)
    assert not embeddings.finish_generation(store)
    for source in (two, empty, passage):
        save(store, source, "new", (0.0, 1.0, 0.0, 0.0))
    # Source updates retain their active vectors until the generation switch.
    assert store.execute(text("SELECT COUNT(*) FROM ml_embeddings WHERE generation='old'")).scalar_one() == 3
    finish(store)
    assert budget(store)["active_generation"] == "new"
    assert budget(store)["staging_generation"] is None
    assert budget(store)["staging_reserved_bytes"] == budget(store)["staging_bytes"] == 0
    assert store.execute(text("SELECT DISTINCT generation FROM ml_embeddings")).scalars().all() == ["new"]
    assert len(embeddings.nearest(store, vector((0.0, 1.0, 0.0, 0.0)), "new")) == 3


@pytest.mark.parametrize("mismatch", ["extraction_identity", "embedding_version", "source_hash", "vector_count"])
def test_staging_completion_requires_coherent_current_source_metadata(store, mismatch):
    source = add_source(store, "source")
    save(store, source)
    save(store, source, "new")
    idle(store)
    if mismatch == "extraction_identity":
        stale = runtime.inference_version({"extractor": {"revision": "stale-extractor"},
                                           "embeddings": {"revision": "new"}})
        store.execute(text("UPDATE ml_sources SET model_version=:version WHERE id='source'"), {"version": stale})
    elif mismatch == "embedding_version":
        store.execute(text("UPDATE ml_sources SET result=json_set(result,'$.embedding_version','old') WHERE id='source'"))
    elif mismatch == "source_hash":
        store.execute(text("UPDATE ml_embeddings SET source_hash='stale' WHERE generation='new'"))
    else:
        store.execute(text("DELETE FROM ml_embeddings WHERE generation='new'"))
    assert not embeddings.finish_generation(store)
    assert budget(store)["active_generation"] == "old"
    assert embeddings.nearest(store, vector(), "old")
    assert not embeddings.nearest(store, vector(), "new")
    save(store, source, "new")
    finish(store)
    assert budget(store)["active_generation"] == "new"
    assert store.execute(text("SELECT DISTINCT generation FROM ml_embeddings")).scalars().all() == ["new"]
    assert embeddings.nearest(store, vector(), "new")


def test_quota_reserves_entire_rebuild_and_disk_pause_keeps_active(store, monkeypatch):
    sources = [add_source(store, name) for name in ("one", "two", "three")]
    for source in sources:
        save(store, source)
    before = budget(store)["embedding_bytes"]
    # One staged vector would fit; the complete rebuild would not.
    with pytest.raises(embeddings.StoragePressure, match="quota"):
        save(store, sources[0], "new", max_bytes=before + VECTOR_BYTES)
    assert budget(store)["active_generation"] == "old"
    assert budget(store)["staging_generation"] is None
    assert budget(store)["embedding_bytes"] == before
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: embeddings.FREE_RESERVE + VECTOR_BYTES)
    with pytest.raises(embeddings.StoragePressure, match="filesystem reserve"):
        save(store, sources[0], "new")
    assert len(embeddings.nearest(store, vector(), "old")) == 3
    # A caller cannot raise the production hard cap through a test override.
    store.execute(text("UPDATE ml_budget SET embedding_bytes=:used WHERE id=1"), {"used": embeddings.MAX_BYTES})
    with pytest.raises(embeddings.StoragePressure, match="quota"):
        save(store, add_source(store, "extra"), max_bytes=2 * embeddings.MAX_BYTES)


def test_invalid_dimensions_windows_and_failed_transaction_leave_prior_vectors(store):
    source = add_source(store, "one")
    save(store, source)
    store.commit()
    with pytest.raises(ValueError, match="dimensions changed"):
        save(store, source, values=(1.0, 0.0))
    with pytest.raises(ValueError, match="finite"):
        embeddings.replace_source(store, source, chunks(source, (float("nan"), 1.0, 0.0)), "old", 3)
    with pytest.raises(ValueError, match="offsets"):
        embeddings.replace_source(store, source, chunks(source) * 2, "old", 3)
    save(store, source, "new")
    store.rollback()
    assert budget(store)["staging_generation"] is None
    assert embeddings.nearest(store, vector(), "old")[0]["similarity"] == pytest.approx(1.0)


def test_candidates_are_index_bounded_and_exclude_private_stale_and_self(store, monkeypatch):
    for number in range(350):
        save(store, add_source(store, f"source{number:04}"))
    queries = []
    @event.listens_for(store.get_bind(), "before_cursor_execute")
    def capture(_conn, _cursor, statement, parameters, *_):
        if "AS candidate JOIN" in statement:
            queries.append((statement, parameters))
    loaded = []
    original = embeddings._values
    def counted(value, dimensions):
        loaded.append(value)
        return original(value, dimensions)
    monkeypatch.setattr(embeddings, "_values", counted)
    matches = embeddings.nearest(store, vector(), "old", ("item", "source0000"), limit=100)
    event.remove(store.get_bind(), "before_cursor_execute", capture)
    assert len(matches) == 8 and len(loaded) <= 257 and len(queries) == 4
    assert all(match["source_id"] != "source0000" for match in matches)
    assert all(match["similarity"] == pytest.approx(1.0) for match in matches)
    for statement, parameters in queries:
        plan = store.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
        assert any("SEARCH ml_embeddings USING INDEX ix_ml_embeddings_bucket" in row[-1] for row in plan)
    private_id, stale_id, deleted_id = [match["source_id"] for match in matches[:3]]
    store.execute(text("UPDATE knowledge_items SET visibility='private' WHERE id=:id"), {"id": private_id})
    store.execute(text("UPDATE knowledge_items SET body='No longer the indexed text.' WHERE id=:id"), {"id": stale_id})
    store.execute(text("DELETE FROM knowledge_items WHERE id=:id"), {"id": deleted_id})
    assert not {private_id, stale_id, deleted_id} & {match["source_id"] for match in embeddings.nearest(store, vector(), "old")}
    assert not embeddings.nearest(store, vector((1.0, 0.0)), "old")
    before = budget(store)["embedding_bytes"]
    finish(store)
    assert budget(store)["embedding_bytes"] == before - 3 * VECTOR_BYTES


def test_staging_deletion_and_late_arrival_are_accounted_for(store):
    source = add_source(store, "original")
    save(store, source)
    save(store, source, "new")
    assert budget(store)["staging_bytes"] == VECTOR_BYTES
    store.execute(text("DELETE FROM knowledge_items WHERE id='original'"))
    embeddings.invalidate_source(store, "item", "original")
    assert budget(store)["staging_bytes"] == budget(store)["embedding_bytes"] == 0
    newcomer = add_source(store, "newcomer")
    idle(store)
    assert not embeddings.finish_generation(store)
    save(store, newcomer, "new")
    finish(store)
    assert embeddings.nearest(store, vector(), "new")[0]["source_id"] == "newcomer"
