"""Migration 0003 folds a real, messy vocabulary into the single-term schema.

Existing databases legitimately contain collisions the new schema forbids: a
name reused as another concept's alias, two concepts differing only by case,
and tag rows pointing at content that no longer exists. Losing a concept during
that fold would be silent data loss, so it is checked here rather than left to
the first production upgrade.
"""

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
INI = str(ROOT / "backend" / "alembic.ini")


def migrate(env: dict, direction: str, target: str) -> None:
    result = subprocess.run(
        [str(ALEMBIC), "-c", INI, direction, target], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def seed_messy_0002(db: Path) -> dict:
    con = sqlite3.connect(db)
    profile, item, document, passage = (uuid.uuid4().hex for _ in range(4))
    con.execute(
        "INSERT INTO profiles (id, token_hash, created_at) VALUES (?,?,datetime('now'))",
        (profile, uuid.uuid4().hex),
    )
    ids = {}
    for name in ["Optima", "optima", "Payments", "Warehouse"]:
        ids[name] = uuid.uuid4().hex
        con.execute("INSERT INTO concepts (id, name) VALUES (?,?)", (ids[name], name))
    # 'optima' already belongs to a concept by name; Payments also claims it.
    con.execute(
        "INSERT INTO concept_aliases (id, concept_id, alias) VALUES (?,?,?)",
        (uuid.uuid4().hex, ids["Payments"], "optima"),
    )
    con.execute(
        "INSERT INTO concept_aliases (id, concept_id, alias) VALUES (?,?,?)",
        (uuid.uuid4().hex, ids["Warehouse"], "whse"),
    )
    con.execute(
        "INSERT INTO knowledge_items (id, kind, body, visibility, author_profile_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        (item, "note", "Optima and whse.", "team", profile),
    )
    con.execute(
        "INSERT INTO documents (id, filename, stored_path, uploader_profile_id, status, uploaded_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (document, "d.txt", "/tmp/d.txt", profile, "extracted"),
    )
    con.execute(
        "INSERT INTO document_passages (id, document_id, ord, text, locator) VALUES (?,?,?,?,?)",
        (passage, document, 0, "Optima passage", "Line 1"),
    )
    for kind, subject in [("item", item), ("passage", passage), ("item", "no-such-item")]:
        con.execute(
            "INSERT INTO item_concepts (id, subject_kind, subject_id, concept_id) VALUES (?,?,?,?)",
            (uuid.uuid4().hex, kind, subject, ids["Optima"]),
        )
    con.commit()
    con.close()
    return ids


def invariants(db: Path) -> dict:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    result = {
        "concepts": con.execute("SELECT COUNT(*) FROM concepts").fetchone()[0],
        "terms": con.execute("SELECT COUNT(*) FROM concept_terms").fetchone()[0],
        "distinct_terms": con.execute("SELECT COUNT(DISTINCT term) FROM concept_terms").fetchone()[0],
        "canonical": con.execute(
            "SELECT COUNT(*) FROM concept_terms WHERE is_canonical = 1"
        ).fetchone()[0],
        "item_tags": con.execute("SELECT COUNT(*) FROM item_concepts").fetchone()[0],
        "passage_tags": con.execute("SELECT COUNT(*) FROM passage_concepts").fetchone()[0],
        "fk_violations": len(con.execute("PRAGMA foreign_key_check").fetchall()),
        "integrity": con.execute("PRAGMA integrity_check").fetchone()[0],
    }
    con.close()
    return result


@pytest.fixture()
def messy_db(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    env = {**os.environ, "MDS_DATA_DIR": str(tmp_path), "MDS_DATABASE_URL": f"sqlite:///{db}"}
    migrate(env, "upgrade", "0002")
    seed_messy_0002(db)
    return db, env


def test_upgrade_folds_colliding_vocabulary_without_losing_concepts(messy_db):
    db, env = messy_db
    migrate(env, "upgrade", "head")
    after = invariants(db)

    assert after["concepts"] == 4, "no concept may be dropped by the fold"
    assert after["canonical"] == 4, "every concept keeps exactly one canonical term"
    assert after["terms"] == after["distinct_terms"], "a term may belong to only one concept"
    assert after["fk_violations"] == 0
    assert after["integrity"] == "ok"
    # The orphan tag is gone; the two valid ones survive on their new tables.
    assert after["item_tags"] == 1
    assert after["passage_tags"] == 1

    con = sqlite3.connect(db)
    terms = dict(con.execute("SELECT term, is_canonical FROM concept_terms"))
    con.close()
    assert "optima" in terms, "the first owner keeps the contested word"
    assert "whse" in terms and terms["whse"] == 0, "a genuine alias survives as an alias"
    # The concept that lost the clash survives under a disambiguated name.
    assert any(t.startswith("optima (") for t in terms)


def test_downgrade_and_reupgrade_round_trips(messy_db):
    db, env = messy_db
    migrate(env, "upgrade", "head")
    before = invariants(db)
    migrate(env, "downgrade", "0002")
    migrate(env, "upgrade", "head")
    assert invariants(db) == before
