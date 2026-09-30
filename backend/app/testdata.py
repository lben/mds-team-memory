"""UAT imports with an optional, transactionally coupled journal in the app DB.

Journal tables are created only by this UAT management command. They do not
change the application/Alembic contract; backups include them and reset clears
them. Object IDs are recorded in the same transaction as their creation, so a
failed or interrupted import is still removable. No evaluation labels are used.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import secrets
import time
import uuid

from sqlalchemy import text

from . import config
from .db import SessionLocal, engine
from .testdata_catalog import add_selection_arguments, selection_from_args


def require_uat():
    if os.environ.get("MDS_ENVIRONMENT") != "uat":
        raise ValueError("TestData requires a deployed UAT environment (MDS_ENVIRONMENT=uat). Run Update UAT first; PROD is refused.")


def initialize():
    require_uat()
    # No foreign keys to live objects: retain the receipt if an item is deleted
    # in the UI, and retain evidence of interrupted/removed batches.
    with engine.begin() as db:
        db.execute(text("""CREATE TABLE IF NOT EXISTS testdata_batches (
          id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, dataset TEXT NOT NULL,
          dataset_sha256 TEXT NOT NULL, selection TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('adding','active','failed','removing','removed')),
          created_at REAL NOT NULL, updated_at REAL NOT NULL, error TEXT)"""))
        db.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS testdata_live_fingerprint
          ON testdata_batches(fingerprint) WHERE state!='removed'"""))
        db.execute(text("""CREATE TABLE IF NOT EXISTS testdata_cases (
          batch_id TEXT NOT NULL REFERENCES testdata_batches(id), case_id TEXT NOT NULL,
          PRIMARY KEY(batch_id,case_id))"""))
        db.execute(text("""CREATE TABLE IF NOT EXISTS testdata_objects (
          batch_id TEXT NOT NULL REFERENCES testdata_batches(id), kind TEXT NOT NULL,
          id TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(kind,id))"""))
        db.execute(text("CREATE INDEX IF NOT EXISTS testdata_objects_batch ON testdata_objects(batch_id)"))


@contextmanager
def mutation_lock():
    require_uat()
    with (config.DATA_DIR / ".testdata.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another TestData add/remove is running; retry after it finishes") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def record(db, batch, kind, identity, metadata):
    db.execute(text("INSERT INTO testdata_objects VALUES (:batch,:kind,:id,:metadata)"),
               dict(batch=batch, kind=kind, id=identity, metadata=json.dumps(metadata)))


def set_state(batch, state, error=None):
    with SessionLocal() as db:
        db.execute(text("UPDATE testdata_batches SET state=:state,error=:error,updated_at=:now WHERE id=:id"),
                   dict(state=state, error=error, now=time.time(), id=batch))
        db.commit()


def import_batch(selection, cases, progress=print):
    from .auth import hash_password
    from .impact import mark_helped
    from .knowledge import process_after_save
    from .models import Account, KnowledgeItem, Profile, Scratchpad
    from .routers.questions import AcceptIn, accept_answer

    initialize()
    batch = "test-" + time.strftime("%Y%m%d-") + uuid.uuid4().hex[:12]
    with mutation_lock():
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            existing = db.execute(text("""SELECT DISTINCT b.id FROM testdata_batches b
              JOIN testdata_cases c ON c.batch_id=b.id
              WHERE b.dataset=:dataset AND b.state!='removed'
              AND c.case_id IN (SELECT value FROM json_each(:cases))"""),
              dict(cases=json.dumps(selection["case_ids"]), dataset=selection["dataset"])).scalars().all()
            if existing:
                raise ValueError(f"Selection overlaps existing batches: {', '.join(existing)}. Remove those batches first.")
            db.execute(text("""INSERT INTO testdata_batches VALUES
              (:id,:fingerprint,:dataset,:sha,:selection,'adding',:now,:now,NULL)"""),
              dict(id=batch, fingerprint=selection["fingerprint"], dataset=selection["dataset"],
                   sha=selection["dataset_sha256"], selection=json.dumps(selection), now=time.time()))
            db.execute(text("INSERT INTO testdata_cases VALUES (:batch,:case)"),
                       [dict(batch=batch, case=c["id"]) for c in cases])
            db.commit()
        progress(f"Batch {batch}: importing {selection['selected_units']} {selection['unit']}, {selection['posts']} posts", flush=True)
        try:
            profiles = {}
            done = 0
            with SessionLocal() as db:
                for case in cases:
                    actors = sorted({p["actor"] for p in case["posts"]}
                                    | {a for p in case["posts"] for a in p.get("helped_by", [])})
                    scope = "capacity" if selection["dataset"] == "capacity" else case["id"]
                    for actor in actors:
                        key = (scope, actor)
                        if key in profiles:
                            continue
                        account = Account(id=uuid.uuid4().hex,
                                          username=f"td-{batch[-12:]}-{len(profiles):04d}",
                                          password_hash=hash_password(secrets.token_urlsafe(32)), is_admin=False)
                        profile = Profile(id=uuid.uuid4().hex, account_id=account.id, claim_locked=True,
                                          display_name=f"TestData {scope}: {actor}"[:80])
                        db.add(account)
                        db.flush()
                        db.add(profile)
                        db.flush()
                        record(db, batch, "account", account.id, {"username": account.username})
                        record(db, batch, "profile", profile.id, {"account_id": account.id})
                        db.commit()
                        profiles[key] = profile.id
                    posts = []
                    for index, post in enumerate(case["posts"]):
                        author = db.get(Profile, profiles[(scope, post["actor"])])
                        identity = uuid.uuid4().hex
                        if post.get("visibility", "team") == "private":
                            # Same representation as the evaluation runner: private
                            # notes are scratchpads and never enter team inference.
                            item = Scratchpad(id=identity, profile_id=author.id, content=post["body"],
                                              name=f"TestData {case['id']}", is_default=False)
                            kind = "scratchpad"
                            metadata = {"profile_id": author.id}
                        else:
                            parent_index = post.get("parent")
                            parent = posts[parent_index] if parent_index is not None else None
                            if parent is not None and not isinstance(parent, KnowledgeItem):
                                raise ValueError("An answer cannot refer to a private scratchpad")
                            item = KnowledgeItem(id=identity, kind=post["kind"], body=post["body"], visibility="team",
                                                 author_profile_id=author.id, parent_id=parent.id if parent else None,
                                                 question_status="open" if post["kind"] == "question" else None)
                            if parent and item.kind == "answer" and parent.question_status == "open":
                                parent.question_status = "answered"
                            kind = "item"
                            metadata = {"author_profile_id": author.id}
                        db.add(item)
                        record(db, batch, kind, identity, metadata)
                        db.commit()
                        posts.append(item)
                        if kind == "item":
                            process_after_save(db, item)
                            if post.get("accepted"):
                                parent = db.get(KnowledgeItem, item.parent_id)
                                asker = db.get(Profile, parent.author_profile_id)
                                accept_answer(parent.id, AcceptIn(answer_id=item.id), asker,
                                              db.get(Account, asker.account_id), db)
                            for actor in post.get("helped_by", []):
                                mark_helped(db, item, db.get(Profile, profiles[(scope, actor)]))
                        done += 1
                        if done == 1 or done % 25 == 0 or done == selection["posts"]:
                            progress(f"Batch {batch}: {done}/{selection['posts']} posts imported", flush=True)
            set_state(batch, "active")
            return status(batch)
        except BaseException as error:
            set_state(batch, "failed", f"{type(error).__name__}: {error}"[:2000])
            progress(f"Import stopped. Batch {batch} is tracked and can be removed.", flush=True)
            raise


def batches():
    initialize()
    with SessionLocal() as db:
        return [dict(row) for row in db.execute(text("""SELECT id,dataset,state,created_at,error,
          json_extract(selection,'$.percent') AS percent,
          json_extract(selection,'$.selected_units') AS selected_units,
          json_extract(selection,'$.posts') AS posts FROM testdata_batches ORDER BY created_at DESC""")).mappings()]


def status(batch):
    initialize()
    with SessionLocal() as db:
        row = db.execute(text("SELECT * FROM testdata_batches WHERE id=:batch"), {"batch": batch}).mappings().first()
        if row is None:
            raise ValueError(f"Unknown batch {batch!r}; use TestData batches")
        result = dict(row)
        result["selection"] = json.loads(result["selection"])
        result["selection"].pop("case_ids")
        params = {"batch": batch}
        result["objects_recorded"] = dict(db.execute(text("""SELECT kind,count(*) FROM testdata_objects
          WHERE batch_id=:batch GROUP BY kind"""), params).all())
        result["remaining_posts"] = db.execute(text("""SELECT count(*) FROM testdata_objects o
          JOIN knowledge_items i ON i.id=o.id WHERE o.batch_id=:batch AND o.kind='item'"""), params).scalar()
        result["processed_posts"] = db.execute(text("""SELECT count(*) FROM testdata_objects o
          JOIN knowledge_items i ON i.id=o.id JOIN ml_sources s ON s.kind='item' AND s.id=i.id
          WHERE o.batch_id=:batch AND o.kind='item' AND s.valid=1 AND s.result!='{}'"""), params).scalar()
        result["pending_jobs"] = db.execute(text("""SELECT count(*) FROM ml_jobs j
          JOIN testdata_objects o ON o.kind=j.source_kind AND o.id=j.source_id
          WHERE o.batch_id=:batch"""), params).scalar()
        result["jobs"] = [dict(r) for r in db.execute(text("""SELECT j.source_kind,j.source_id,j.attempts,j.error
          FROM ml_jobs j JOIN testdata_objects o ON o.kind=j.source_kind AND o.id=j.source_id
          WHERE o.batch_id=:batch ORDER BY j.error IS NULL,j.created_at LIMIT 20"""), params).mappings()]
        result["global_jobs"] = db.execute(text("SELECT count(*) FROM ml_jobs")).scalar()
        state = db.execute(text("SELECT status,worker_lease_until,backfill_kind FROM ml_state WHERE id=1")).mappings().first()
        result["worker"] = dict(state) if state else {}
        result["worker_alive"] = bool(state and (state["worker_lease_until"] or 0) > time.time())
        result["findings"] = [dict(r) for r in db.execute(text("""SELECT f.kind,f.state,count(DISTINCT f.key) AS count
          FROM ml_findings f JOIN ml_evidence e ON e.finding_key=f.key
          JOIN testdata_objects o ON o.kind=e.source_kind AND o.id=e.source_id
          WHERE o.batch_id=:batch GROUP BY f.kind,f.state"""), params).mappings()]
        result["embeddings"] = db.execute(text("""SELECT count(*) FROM ml_embeddings e
          JOIN testdata_objects o ON o.kind=e.source_kind AND o.id=e.source_id
          WHERE o.batch_id=:batch"""), params).scalar()
        result["evidence_rows"] = db.execute(text("""SELECT count(*) FROM ml_evidence e
          JOIN testdata_objects o ON o.kind=e.source_kind AND o.id=e.source_id
          WHERE o.batch_id=:batch"""), params).scalar()
        vocabulary = db.execute(text("SELECT count(*) FROM ml_jobs WHERE source_kind='vocabulary'")).scalar()
        settled = not result["pending_jobs"] and not vocabulary and not (state and state["backfill_kind"])
        if result["state"] == "removing":
            complete = settled and not result["remaining_posts"] and not result["embeddings"] and not result["evidence_rows"]
        else:
            complete = (result["state"] in ("active", "removed") and settled
                        and result["processed_posts"] == result["remaining_posts"])
        result["processing"] = "complete" if complete else "pending" if result["worker_alive"] else "worker-stopped"
        if any(j["error"] for j in result["jobs"]):
            result["processing"] = "error"
        if result["state"] == "removing" and result["error"]:
            result["processing"] = "cleanup-blocked"
        return result


def wait(batch, timeout=1800, progress=print):
    deadline, last_print = time.monotonic() + timeout, 0
    while True:
        result = status(batch)
        if result["processing"] == "complete":
            if result["state"] == "removing":
                set_state(batch, "removed")
                result["state"] = "removed"
            return result
        if result["state"] in ("adding", "failed"):
            raise ValueError(f"Batch {batch} is {result['state']}; remove the tracked batch before importing again")
        if result["processing"] == "cleanup-blocked":
            raise ValueError(f"Batch {batch}: {result['error']}. Resolve the preservation issue and run remove again.")
        if result["processing"] in ("error", "worker-stopped"):
            raise ValueError(f"Batch {batch}: {result['processing']}; inspect ML status/logs. Data remains tracked.")
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(f"Batch {batch} is still pending. Run status --batch {batch} --wait --timeout <seconds> again.")
        if now - last_print >= 15:
            progress(f"Batch {batch}: {result['processed_posts']}/{result['remaining_posts']} posts processed; {result['pending_jobs']} batch jobs; {result['global_jobs']} global jobs", flush=True)
            last_print = now
        time.sleep(min(2, max(0, deadline - now)))


def _objects(db, batch):
    rows = db.execute(text("SELECT kind,id,metadata FROM testdata_objects WHERE batch_id=:id ORDER BY rowid"), {"id": batch})
    return [(kind, identity, json.loads(metadata)) for kind, identity, metadata in rows]


def _check_preservation(db, batch):
    from .models import Account, Document, KnowledgeItem, Profile, Scratchpad
    objects = _objects(db, batch)
    ids = {kind: {identity for k, identity, _ in objects if k == kind}
           for kind in ("item", "profile", "account", "scratchpad")}
    for kind, identity, meta in objects:
        if kind == "profile":
            profile = db.get(Profile, identity)
            if profile and profile.account_id != meta["account_id"]:
                raise ValueError(f"Preserving rebound test profile {identity}; cleanup refused")
        elif kind == "account":
            account = db.get(Account, identity)
            if account and (account.is_admin or account.username != meta["username"]):
                raise ValueError(f"Preserving changed test account {identity}; cleanup refused")
        elif kind == "scratchpad":
            pad = db.get(Scratchpad, identity)
            if pad and pad.profile_id != meta["profile_id"]:
                raise ValueError(f"Preserving reassigned scratchpad {identity}; cleanup refused")
    # SQL subqueries avoid SQLite's bind-variable limit on a 50,000-item batch.
    reassigned = db.execute(text("""SELECT i.id FROM testdata_objects o JOIN knowledge_items i ON i.id=o.id
      WHERE o.batch_id=:batch AND o.kind='item'
      AND i.author_profile_id!=json_extract(o.metadata,'$.author_profile_id') LIMIT 1"""), {"batch": batch}).scalar()
    if reassigned:
        raise ValueError(f"Preserving reassigned test item {reassigned}; cleanup refused")
    unowned = db.execute(text("""SELECT i.id FROM knowledge_items i WHERE
      (i.parent_id IN (SELECT id FROM testdata_objects WHERE batch_id=:batch AND kind='item')
       OR i.author_profile_id IN (SELECT id FROM testdata_objects WHERE batch_id=:batch AND kind='profile'))
      AND i.id NOT IN (SELECT id FROM testdata_objects WHERE batch_id=:batch AND kind='item') LIMIT 1"""), {"batch": batch}).scalar()
    if unowned:
        raise ValueError(f"Preserving non-test contribution {unowned} attached to this batch; cleanup refused")
    profiles = ids["profile"]
    if profiles:
        if db.query(Document.id).filter(Document.uploader_profile_id.in_(profiles)).first():
            raise ValueError("Preserving uploads belonging to a test profile; cleanup refused")
        if db.query(Scratchpad.id).filter(Scratchpad.profile_id.in_(profiles), Scratchpad.id.notin_(ids["scratchpad"] or [""])).first():
            raise ValueError("Preserving a non-test scratchpad belonging to a test profile; cleanup refused")
        if db.query(Profile.id).filter(Profile.account_id.in_(ids["account"]), Profile.id.notin_(profiles)).first():
            raise ValueError("Preserving a non-test profile linked to a test account; cleanup refused")
    return objects


def remove_batch(batch, progress=print):
    initialize()
    with mutation_lock():
        try:
            return _remove_batch(batch, progress)
        except Exception as error:
            # Record failures while still holding the mutation lock. A second
            # command refused by the lock must not poison an in-progress batch.
            with SessionLocal() as db:
                db.execute(text("""UPDATE testdata_batches SET error=:error,updated_at=:now
                  WHERE id=:id AND state='removing'"""),
                           dict(id=batch, error=f"{type(error).__name__}: {error}"[:2000], now=time.time()))
                db.commit()
            raise


def _remove_batch(batch, progress=print):
    from .knowledge import delete_item
    from .models import Account, ExpertiseMapping, ImpactEvent, KnowledgeItem, Notification, Profile, Relationship, Scratchpad
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.execute(text("SELECT state FROM testdata_batches WHERE id=:id"), {"id": batch}).first()
        if row is None:
            raise ValueError(f"Unknown batch {batch!r}")
        if row[0] == "removed":
            db.commit()
            return status(batch)
        objects = _check_preservation(db, batch)
        db.execute(text("UPDATE testdata_batches SET state='removing',error=NULL,updated_at=:now WHERE id=:id"), dict(now=time.time(), id=batch))
        db.commit()
    # Delete leaves before parents. Check each parent under a write lock so
    # a human answer added concurrently is never swept up by delete_item.
    item_ids = [i for k, i, _ in objects if k == "item"]
    item_metadata = {i: meta for k, i, meta in objects if k == "item"}
    for index, identity in enumerate(reversed(item_ids)):
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            item = db.get(KnowledgeItem, identity)
            if item:
                if db.query(KnowledgeItem.id).filter(KnowledgeItem.parent_id == identity).first():
                    raise ValueError(f"Preserving attached contribution on {identity}; cleanup paused")
                expected = item_metadata[identity]
                if item.author_profile_id != expected["author_profile_id"]:
                    raise ValueError(f"Preserving reassigned item {identity}; cleanup paused")
                delete_item(db, item)
            else:
                db.commit()
        if index == 0 or (index + 1) % 25 == 0 or index + 1 == len(item_ids):
            progress(f"Batch {batch}: {index + 1}/{len(item_ids)} item removals checked", flush=True)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        objects = _check_preservation(db, batch)
        profiles = [i for k, i, _ in objects if k == "profile"]
        for kind, identity, _ in objects:
            if kind == "scratchpad":
                pad = db.get(Scratchpad, identity)
                if pad:
                    db.delete(pad)
        db.flush()
        if profiles:
            db.query(ExpertiseMapping).filter(ExpertiseMapping.profile_id.in_(profiles)).delete(synchronize_session=False)
            db.query(Notification).filter(Notification.profile_id.in_(profiles)).delete(synchronize_session=False)
            db.query(ImpactEvent).filter((ImpactEvent.beneficiary_profile_id.in_(profiles)) | (ImpactEvent.actor_profile_id.in_(profiles))).delete(synchronize_session=False)
            db.query(Relationship).filter(((Relationship.src_kind == 'profile') & Relationship.src_id.in_(profiles)) | ((Relationship.dst_kind == 'profile') & Relationship.dst_id.in_(profiles))).delete(synchronize_session=False)
        # Profile deletion invalidates ML through the normal DB triggers.
        for kind in ("profile", "account"):
            model = Profile if kind == "profile" else Account
            for k, identity, _ in objects:
                if k == kind:
                    obj = db.get(model, identity)
                    if obj:
                        db.delete(obj)
            db.flush()
        db.commit()
    progress(f"Batch {batch}: imported content and identities removed; waiting for ML withdrawal", flush=True)
    return status(batch)


def main(argv=None):
    parser = argparse.ArgumentParser(description="UAT-only TestData server command; normally invoked by TestData.cmd")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add_selection_arguments(add)
    add.add_argument("--expected-fingerprint", required=True)
    commands.add_parser("batches")
    check = commands.add_parser("status")
    check.add_argument("--batch", required=True)
    check.add_argument("--wait", action="store_true")
    check.add_argument("--timeout", type=float, default=1800)
    remove = commands.add_parser("remove")
    group = remove.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch")
    group.add_argument("--all-batches", action="store_true")
    remove.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args(argv)
    try:
        require_uat()
        if hasattr(args, "timeout") and (not 0 < args.timeout <= 604800):
            raise ValueError("--timeout must be positive and at most 604800 seconds")
        if args.command == "add":
            selection, cases = selection_from_args(args)
            if selection["fingerprint"] != args.expected_fingerprint:
                raise ValueError("Local and deployed datasets differ; run Update UAT from this checkout before importing")
            result = import_batch(selection, cases)
        elif args.command == "batches":
            result = batches()
        elif args.command == "status":
            result = wait(args.batch, args.timeout) if args.wait else status(args.batch)
        else:
            selected = [args.batch] if args.batch else [b["id"] for b in batches() if b["state"] != "removed"]
            result = []
            for batch in selected:
                remove_batch(batch)
                result.append(wait(batch, args.timeout))
        print(json.dumps(result, indent=2), flush=True)
        return 0
    except Exception as error:
        print(f"TestData failed: {error}", flush=True)
        return 1
