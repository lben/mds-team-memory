"""Real SQLite transactions, processes, and migration paths for durable ML work."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.ml import queue

ROOT = Path(__file__).resolve().parents[2]
FIXED_SQLITE = queue.sqlite_is_safe(sqlite3.sqlite_version_info)
requires_fixed_sqlite = pytest.mark.skipif(not FIXED_SQLITE, reason="Worker requires the SQLite WAL-reset fix")


def migrate(path, direction, target):
    env = {**os.environ, "MDS_DATA_DIR": str(path.parent), "MDS_DATABASE_URL": f"sqlite:///{path}"}
    result = subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"),
                             direction, target], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@pytest.fixture(scope="module")
def queue_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("ml-queue-template") / "db.sqlite3"
    migrate(path, "upgrade", "0007")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO profiles(id,claim_locked,created_at) VALUES ('author',0,datetime('now'))")
        conn.execute("INSERT INTO accounts(id,username,password_hash,is_admin,created_at) VALUES ('account','a','unused',0,datetime('now'))")
        conn.execute("INSERT INTO documents(id,filename,stored_path,uploader_profile_id,uploaded_at) VALUES ('doc','file.txt','unused','author',datetime('now'))")
    return path


@pytest.fixture()
def queue_db(queue_template, tmp_path):
    path = tmp_path / "db.sqlite3"
    source, target = sqlite3.connect(queue_template), sqlite3.connect(path)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    return path


@pytest.fixture(scope="module")
def current_queue_template(queue_template, tmp_path_factory):
    """Current worker code needs the current application schema, not 0007."""
    path = tmp_path_factory.mktemp("ml-current-template") / "db.sqlite3"
    with sqlite3.connect(queue_template) as source, sqlite3.connect(path) as target:
        source.backup(target)
    migrate(path, "upgrade", "head")
    return path


@pytest.fixture
def current_queue_db(current_queue_template, tmp_path):
    path = tmp_path / "db.sqlite3"
    with sqlite3.connect(current_queue_template) as source, sqlite3.connect(path) as target:
        source.backup(target)
    return path


def add_item(conn, item_id="item", visibility="team", kind="note"):
    conn.execute("""INSERT INTO knowledge_items(id,kind,body,visibility,author_profile_id,created_at,updated_at)
      VALUES (?,?,'Initial source text',?,'author',datetime('now'),datetime('now'))""", (item_id, kind, visibility))


def job(conn, kind="item", source_id="item"):
    cursor = conn.execute("SELECT * FROM ml_jobs WHERE source_kind=? AND source_id=?", (kind, source_id))
    row = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), row)) if row else None


def process(path, code):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
    return subprocess.run([sys.executable, "-c", code, str(path)], env=env,
                          capture_output=True, text=True, timeout=30, check=True)


def test_source_changes_and_jobs_commit_or_rollback_together(queue_db):
    writer, reader = connect(queue_db), connect(queue_db)
    try:
        writer.execute("BEGIN IMMEDIATE")
        add_item(writer)
        assert job(writer)["generation"] == 1
        assert job(reader) is None
        writer.rollback()
        assert job(reader) is None
        add_item(writer)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE knowledge_items SET body='Changed' WHERE id='item'")
        assert job(writer)["generation"] == 2
        assert job(reader)["generation"] == 1
        writer.rollback()
        assert job(reader)["generation"] == 1
        writer.execute("DELETE FROM knowledge_items WHERE id='item'")
        assert job(reader)["generation"] == 2
        assert reader.execute("SELECT 1 FROM knowledge_items").fetchone() is None
    finally:
        writer.close()
        reader.close()


def test_only_semantic_team_changes_enqueue(queue_db):
    conn = connect(queue_db)
    try:
        add_item(conn)
        add_item(conn, "private", "private")
        assert job(conn, source_id="private") is None
        conn.execute("UPDATE knowledge_items SET body='Private edit' WHERE id='private'")
        assert job(conn, source_id="private") is None
        conn.execute("DELETE FROM knowledge_items WHERE id='private'")
        assert job(conn, source_id="private") is None
        conn.execute("UPDATE knowledge_items SET body=body, updated_at=datetime('now'), normalized_hash='hash' WHERE id='item'")
        assert job(conn)["generation"] == 1
        add_item(conn, "parent", kind="question")
        conn.execute("INSERT INTO document_passages(id,document_id,ord,text,locator) VALUES ('passage','doc',0,'Passage','Line 1')")
        conn.execute("INSERT INTO profiles(id,claim_locked,created_at) VALUES ('other',0,datetime('now'))")
        changes = {"body": "Edited source", "visibility": "private", "kind": "answer",
                   "author_profile_id": "other", "parent_id": "parent", "source_document_id": "doc",
                   "source_passage_id": "passage", "source_item_id": "origin", "group_id": "group",
                   "accepted_answer_id": "accepted", "question_status": "resolved", "correction_state": "adopted"}
        generation = 1
        for column, value in changes.items():
            if column != "visibility":
                conn.execute("UPDATE knowledge_items SET visibility='team' WHERE id='item'")
                generation = job(conn)["generation"]
            conn.execute(f"UPDATE knowledge_items SET {column}=? WHERE id='item'", (value,))
            generation += 1
            assert job(conn)["generation"] == generation, column
        conn.execute("DELETE FROM ml_jobs")
        conn.execute("UPDATE knowledge_items SET visibility='private' WHERE id='item'")
        assert job(conn) is not None, "Making published text private must invalidate it"
    finally:
        conn.close()


def test_passage_outcome_and_account_triggers(queue_db):
    conn = connect(queue_db)
    try:
        add_item(conn)
        conn.execute("INSERT INTO document_passages(id,document_id,ord,text,locator) VALUES ('passage','doc',0,'Passage','Line 1')")
        conn.execute("UPDATE document_passages SET text=text WHERE id='passage'")
        assert job(conn, "passage", "passage")["generation"] == 1
        conn.execute("UPDATE document_passages SET text='New passage',locator='Line 2' WHERE id='passage'")
        assert job(conn, "passage", "passage")["generation"] == 2
        conn.execute("DELETE FROM documents WHERE id='doc'")
        assert job(conn, "passage", "passage")["generation"] == 3
        conn.execute("UPDATE profiles SET account_id='account' WHERE id='author'")
        assert job(conn, "profile", "author")["generation"] == 1
        conn.execute("UPDATE profiles SET display_name='Display only',account_id=account_id WHERE id='author'")
        assert job(conn, "profile", "author")["generation"] == 1
        conn.execute("""INSERT INTO impact_events(id,event_type,beneficiary_profile_id,item_id,points,dedup_key,created_at)
          VALUES ('outcome','helped','author','item',1,'unique',datetime('now'))""")
        assert job(conn)["generation"] == 2
        assert job(conn, "profile", "author")["generation"] == 2
        conn.execute("DELETE FROM impact_events WHERE id='outcome'")
        assert job(conn)["generation"] == 3
        assert job(conn, "profile", "author")["generation"] == 3
        conn.execute("DELETE FROM accounts WHERE id='account'")
        assert job(conn, "profile", "author")["generation"] == 4
        conn.execute("""INSERT INTO impact_events(id,event_type,beneficiary_profile_id,item_id,points,dedup_key,created_at)
          VALUES ('null-item','sme_endorsed','author',NULL,1,'null-item',datetime('now'))""")
        assert conn.execute("SELECT COUNT(*) FROM ml_jobs WHERE source_id IS NULL").fetchone()[0] == 0
    finally:
        conn.close()


def test_session_enqueue_is_transactional_and_retains_live_lease(queue_db):
    engine = create_engine(f"sqlite:///{queue_db}")
    conn = connect(queue_db)
    try:
        with Session(engine) as db:
            queue.enqueue(db, "vocabulary", "all")
            db.rollback()
        assert job(conn, "vocabulary", "all") is None
        with Session(engine) as db, db.begin():
            queue.enqueue(db, "vocabulary", "all")
        conn.execute("UPDATE ml_jobs SET lease_token='active',lease_until=?,attempts=4,error='prior'", (time.time() + 600,))
        with Session(engine) as db, db.begin():
            queue.enqueue(db, "vocabulary", "all")
        row = job(conn, "vocabulary", "all")
        assert row["generation"] == 2 and row["lease_token"] == "active"
        assert row["attempts"] == 0 and row["error"] is None
    finally:
        engine.dispose()
        conn.close()


def test_additive_migration_round_trip_preserves_sources_and_fts(queue_db):
    conn = connect(queue_db)
    add_item(conn)
    conn.execute("INSERT INTO concepts(id) VALUES ('concept')")
    conn.execute("INSERT INTO concept_terms(id,concept_id,term,display,is_canonical) VALUES ('term','concept','source','Source',1)")
    conn.execute("INSERT INTO item_concepts(id,item_id,concept_id) VALUES ('tag','item','concept')")
    before = conn.execute("SELECT * FROM knowledge_items").fetchall()
    conn.close()
    migrate(queue_db, "downgrade", "0006")
    conn = connect(queue_db)
    assert conn.execute("SELECT * FROM knowledge_items").fetchall() == before
    assert conn.execute("SELECT COUNT(*) FROM item_concepts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH 'source'").fetchone()[0] == 1
    add_item(conn, "during-downgrade")
    conn.close()
    migrate(queue_db, "upgrade", "0007")
    conn = connect(queue_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT backfill_kind FROM ml_state").fetchone()[0] == "item"
        conn.execute("UPDATE knowledge_items SET body='After reupgrade' WHERE id='item'")
        assert job(conn)["generation"] == 1
    finally:
        conn.close()


@requires_fixed_sqlite
@pytest.mark.parametrize("target", ["0010", "0009"])
def test_identity_policy_downgrade_requeues_existing_sources(queue_db, target):
    migrate(queue_db, "upgrade", "0014")
    with connect(queue_db) as conn:
        add_item(conn, "first")
        add_item(conn, "second")
        conn.execute("DELETE FROM ml_jobs")
        conn.execute("UPDATE ml_state SET backfill_kind=NULL,backfill_cursor='' WHERE id=1")
        generation = conn.execute("SELECT backfill_generation FROM ml_state WHERE id=1").fetchone()[0]
        before = conn.execute("SELECT id,body FROM knowledge_items ORDER BY id").fetchall()
    migrate(queue_db, "downgrade", target)
    conn = queue.connect_worker(queue_db)
    try:
        assert conn.execute("SELECT backfill_generation FROM ml_state WHERE id=1").fetchone()[0] > generation
        worker = queue.acquire_worker(conn)
        assert queue.backfill_page(conn, worker) == 2
        assert job(conn, source_id="first") and job(conn, source_id="second")
        assert conn.execute("SELECT id,body FROM knowledge_items ORDER BY id").fetchall() == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_worker_refuses_unsafe_sqlite_and_sets_connection_limits(queue_db):
    assert all(queue.sqlite_is_safe(version) for version in ((3, 44, 6), (3, 50, 7), (3, 51, 3), (3, 53, 1)))
    assert not any(queue.sqlite_is_safe(version) for version in ((3, 44, 5), (3, 45, 1), (3, 50, 6), (3, 51, 2)))
    if not FIXED_SQLITE:
        with pytest.raises(RuntimeError, match="WAL-reset fix"):
            queue.connect_worker(queue_db)
        return
    conn = queue.connect_worker(queue_db)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 250
        assert conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 1000
    finally:
        conn.close()


@requires_fixed_sqlite
def test_edited_or_deleted_claim_cannot_publish_and_new_work_is_ready(queue_db):
    writer = connect(queue_db)
    worker = queue.connect_worker(queue_db)
    engine = create_engine(f"sqlite:///{queue_db}", connect_args={"timeout": 0.25})
    try:
        add_item(writer)
        token = queue.acquire_worker(worker)
        claimed = queue.claim_next(worker, token)
        assert claimed and not worker.in_transaction
        writer.execute("UPDATE knowledge_items SET body='New source' WHERE id='item'")
        assert job(writer)["lease_token"] == claimed.lease_token
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            assert not queue.owns_claim(db, claimed)
            assert not queue.complete_claim(db, claimed)
            db.commit()
        newer = queue.claim_next(worker, token)
        assert newer.generation == claimed.generation + 1
        writer.execute("DELETE FROM knowledge_items WHERE id='item'")
        queue.retry_claim(worker, newer, "obsolete")
        deleted = queue.claim_next(worker, token)
        assert deleted.generation == newer.generation + 1
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            assert queue.owns_claim(db, deleted)
            db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))
            assert queue.complete_claim(db, deleted)
            db.rollback()
        assert job(writer) is not None, "A failed canonical commit must retain the job"
        assert writer.execute("SELECT revision FROM ml_state").fetchone()[0] == 0
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            assert queue.owns_claim(db, deleted)
            db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))
            assert queue.complete_claim(db, deleted)
            db.commit()
        assert job(writer) is None
        assert writer.execute("SELECT revision FROM ml_state").fetchone()[0] == 1
    finally:
        writer.close()
        worker.close()
        engine.dispose()


@requires_fixed_sqlite
def test_separate_worker_process_cannot_claim_until_owner_expires(queue_db):
    writer, worker = connect(queue_db), queue.connect_worker(queue_db)
    try:
        add_item(writer)
        owner = queue.acquire_worker(worker)
        result = process(queue_db, """import sys
from app.ml import queue
c=queue.connect_worker(sys.argv[1])
assert queue.acquire_worker(c) is None
c.close()
""")
        assert result.returncode == 0
        claimed = queue.claim_next(worker, owner)
        writer.execute("UPDATE ml_state SET worker_lease_until=0 WHERE id=1")
        writer.execute("UPDATE ml_jobs SET lease_until=0")
        result = process(queue_db, """import json,os,sys
from dataclasses import asdict
from app.ml import queue
c=queue.connect_worker(sys.argv[1])
owner=queue.acquire_worker(c)
assert owner
claim=queue.claim_next(c,owner)
assert claim
print(json.dumps(asdict(claim)),flush=True)
os._exit(0)
""")
        crashed = queue.Claim(**json.loads(result.stdout))
        assert crashed.lease_token != claimed.lease_token
        assert not queue.retry_claim(worker, claimed, "old worker")
        assert not queue.renew_worker(worker, owner)
        writer.execute("UPDATE ml_state SET worker_lease_until=0 WHERE id=1")
        writer.execute("UPDATE ml_jobs SET lease_until=0")
        replacement = queue.acquire_worker(worker)
        recovered = queue.claim_next(worker, replacement)
        assert recovered.generation == crashed.generation
        assert recovered.lease_token != crashed.lease_token
        assert job(writer)["lease_token"] == recovered.lease_token
    finally:
        writer.close()
        worker.close()


@requires_fixed_sqlite
def test_claim_race_allows_one_lease_and_short_busy_timeout_rolls_back(queue_db):
    writer, worker = connect(queue_db), queue.connect_worker(queue_db)
    try:
        add_item(writer)
        owner = queue.acquire_worker(worker)
        script = """import json,sys
from app.ml import queue
c=queue.connect_worker(sys.argv[1])
claimed=queue.claim_next(c,sys.argv[2])
print(json.dumps(None if claimed is None else claimed.lease_token))
"""
        env = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
        children = [subprocess.Popen([sys.executable, "-c", script, str(queue_db), owner],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        claimed = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            claimed.append(json.loads(stdout))
        assert sum(value is not None for value in claimed) == 1
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            queue.claim_next(worker, owner)
        assert time.monotonic() - started < 1.5
        assert not worker.in_transaction
        writer.rollback()
        assert queue.renew_worker(worker, owner)
    finally:
        writer.close()
        worker.close()


@requires_fixed_sqlite
def test_failure_is_delayed_and_does_not_block_later_jobs(queue_db):
    conn = queue.connect_worker(queue_db)
    try:
        add_item(conn, "a")
        add_item(conn, "b")
        owner = queue.acquire_worker(conn)
        claim = queue.claim_next(conn, owner)
        assert claim.source_id == "a"
        assert queue.retry_claim(conn, claim, "bad source" * 1000)
        failed = job(conn, source_id="a")
        assert failed["attempts"] == 1 and len(failed["error"]) == 2000
        assert time.time() < failed["available_at"] <= time.time() + 2
        assert queue.claim_next(conn, owner).source_id == "b"
        conn.execute("UPDATE ml_jobs SET available_at=0,attempts=500 WHERE source_id='a'")
        retry = queue.claim_next(conn, owner)
        assert queue.retry_claim(conn, retry, "again")
        assert 299 < job(conn, source_id="a")["available_at"] - time.time() <= 300
        conn.execute("UPDATE knowledge_items SET body='Corrected source' WHERE id='a'")
        assert queue.claim_next(conn, owner).source_id == "a"
        assert job(conn, source_id="a")["attempts"] == 0
    finally:
        conn.close()


@requires_fixed_sqlite
def test_backfill_is_pageable_durable_and_cannot_displace_live_jobs(queue_db):
    conn = queue.connect_worker(queue_db)
    try:
        with conn:
            for i in range(53):
                add_item(conn, f"item-{i:03}")
        conn.execute("DELETE FROM ml_jobs")
        conn.execute("UPDATE knowledge_items SET body='Live change' WHERE id='item-001'")
        owner = queue.acquire_worker(conn)
        live = queue.claim_next(conn, owner)
        assert queue.backfill_page(conn, owner) == 25
        assert conn.execute("SELECT COUNT(*) FROM ml_jobs WHERE source_kind='item'").fetchone()[0] == 25
        assert job(conn, kind="profile", source_id="author") is not None
        assert job(conn, source_id="item-001")["lease_token"] == live.lease_token
        assert job(conn, source_id="item-001")["priority"] == 0
        assert job(conn, source_id="item-001")["generation"] == live.generation
        queue.release_worker(conn, owner)
        conn.close()
        conn = queue.connect_worker(queue_db)
        owner = queue.acquire_worker(conn)
        assert conn.execute("SELECT backfill_cursor FROM ml_state").fetchone()[0] == "item-024"
        assert queue.backfill_page(conn, owner) == 25
        assert queue.backfill_page(conn, owner) == 3
        assert queue.backfill_page(conn, owner) == 0
        assert conn.execute("SELECT COUNT(*) FROM ml_jobs WHERE source_kind='item'").fetchone()[0] == 53
        assert job(conn, kind="profile", source_id="author") is not None
        assert conn.execute("SELECT backfill_kind FROM ml_state").fetchone()[0] is None
    finally:
        conn.close()


@requires_fixed_sqlite
def test_passive_checkpoint_backs_off_for_large_pinned_wal(queue_db):
    writer, reader = queue.connect_worker(queue_db), connect(queue_db)
    try:
        writer.execute("CREATE TABLE test_pressure(id INTEGER PRIMARY KEY, data BLOB)")
        writer.execute("INSERT INTO test_pressure VALUES(1,?)", (b"x",))
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM test_pressure").fetchall()
        for value in range(17):
            writer.execute("UPDATE test_pressure SET data=? WHERE id=1", (bytes([value]) * (4 * 1024 * 1024),))
        assert queue.checkpoint_between_batches(writer)
        reader.rollback()
        assert not queue.checkpoint_between_batches(writer)
        assert writer.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        reader.close()
        writer.close()
