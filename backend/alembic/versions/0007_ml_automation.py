"""Durable, coalesced maintenance jobs and worker state.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NOW = "CAST(strftime('%s', 'now') AS REAL)"
ITEM_COLUMNS = (
    "kind", "body", "visibility", "author_profile_id", "parent_id",
    "source_document_id", "source_passage_id", "source_item_id", "group_id",
    "accepted_answer_id", "question_status", "correction_state",
)
PASSAGE_COLUMNS = ("text", "document_id", "ord", "locator")


def _enqueue(kind: str, source: str, from_clause: str = "") -> str:
    # A changed source supersedes its result, never its in-flight lease.
    return f"""
      INSERT INTO ml_jobs(source_kind, source_id, available_at, created_at, updated_at)
      SELECT '{kind}', {source}, {NOW}, {NOW}, {NOW} {from_clause} WHERE {source} IS NOT NULL
      ON CONFLICT(source_kind, source_id) DO UPDATE SET
        generation=ml_jobs.generation+1, priority=0, available_at={NOW},
        attempts=0, error=NULL, updated_at={NOW};
    """


def _trigger(name: str, action: str, table: str, body: str, when: str = "") -> None:
    op.execute(f"CREATE TRIGGER {name} AFTER {action} ON {table} {when} BEGIN {body} END")


def upgrade() -> None:
    op.execute("CREATE INDEX ix_knowledge_items_visibility_created_at ON knowledge_items(visibility,created_at)")
    _derived_schema()
    op.execute("""CREATE TABLE ml_jobs (
      source_kind TEXT NOT NULL CHECK(source_kind IN ('item','passage','profile','vocabulary')),
      source_id TEXT NOT NULL,
      generation INTEGER NOT NULL DEFAULT 1,
      priority INTEGER NOT NULL DEFAULT 0,
      available_at REAL NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      error TEXT,
      lease_token TEXT,
      lease_until REAL,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL,
      PRIMARY KEY(source_kind, source_id)
    )""")
    op.execute("CREATE INDEX ix_ml_jobs_ready ON ml_jobs(priority, available_at, created_at)")
    op.execute("""CREATE TABLE ml_state (
      id INTEGER PRIMARY KEY CHECK(id=1),
      revision INTEGER NOT NULL DEFAULT 0,
      automation_enabled BOOLEAN NOT NULL DEFAULT 0,
      pipeline_version TEXT NOT NULL DEFAULT '',
      decision_policy TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'stopped',
      worker_token TEXT,
      worker_lease_until REAL,
      backfill_kind TEXT,
      backfill_cursor TEXT NOT NULL DEFAULT '',
      backfill_generation INTEGER NOT NULL DEFAULT 1
    )""")
    op.execute("INSERT INTO ml_state(id, backfill_kind) VALUES (1, 'item')")

    for kind, table, columns in (
        ("item", "knowledge_items", ITEM_COLUMNS),
        ("passage", "document_passages", PASSAGE_COLUMNS),
    ):
        _trigger(f"ml_{kind}_insert", "INSERT", table, _enqueue(kind, "new.id"),
                 "WHEN new.visibility='team'" if kind == "item" else "")
        _trigger(f"ml_{kind}_delete", "DELETE", table, _enqueue(kind, "old.id"),
                 "WHEN old.visibility='team'" if kind == "item" else "")
        changed = " OR ".join(f"new.{column} IS NOT old.{column}" for column in columns)
        eligibility = "(new.visibility='team' OR old.visibility='team') AND " if kind == "item" else ""
        _trigger(f"ml_{kind}_update", "UPDATE OF " + ",".join(columns), table,
                 _enqueue(kind, "new.id"), f"WHEN {eligibility}({changed})")

    for action, row in (("INSERT", "new"), ("DELETE", "old")):
        _trigger(f"ml_outcome_{action.lower()}", action, "impact_events",
                 _enqueue("item", f"{row}.item_id") + _enqueue("profile", f"{row}.beneficiary_profile_id"))
        _trigger(f"ml_profile_{action.lower()}", action, "profiles", _enqueue("profile", f"{row}.id"),
                 f"WHEN {row}.account_id IS NOT NULL")
    _trigger("ml_profile_update", "UPDATE OF account_id", "profiles",
             _enqueue("profile", "new.id"), "WHEN new.account_id IS NOT old.account_id")
    _invalidation_triggers()
    # Legacy migrations did not install the Profile.account_id foreign key.
    _trigger("ml_account_delete", "DELETE", "accounts",
             _enqueue("profile", "id", "FROM (SELECT id FROM profiles WHERE account_id=old.id)"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_items_visibility_created_at")
    for name in ("item_update", "item_delete", "passage_update", "passage_delete",
                 "outcome_insert", "outcome_delete", "profile_update", "account_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS ml_invalidate_{name}")
    for kind in ("item", "passage", "profile"):
        for action in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS ml_{kind}_{action}")
    for action in ("insert", "delete"):
        op.execute(f"DROP TRIGGER IF EXISTS ml_outcome_{action}")
    op.execute("DROP TRIGGER IF EXISTS ml_account_delete")
    op.execute("DROP TABLE ml_jobs")
    op.execute("DROP TABLE ml_state")
    for table in ("ml_evidence", "ml_embeddings", "ml_sources", "ml_findings", "ml_overrides", "ml_budget"):
        op.execute(f"DROP TABLE {table}")


def _derived_schema():
    definitions = {
        "ml_sources": """kind TEXT NOT NULL,id TEXT NOT NULL,content_hash TEXT NOT NULL,
          valid BOOLEAN NOT NULL,model_version TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',
          updated_at DATETIME NOT NULL,PRIMARY KEY(kind,id)""",
        "ml_findings": """key TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
          score FLOAT NOT NULL,calibrated BOOLEAN NOT NULL,policy_version TEXT NOT NULL,
          canonical_id TEXT,created_at DATETIME NOT NULL,updated_at DATETIME NOT NULL""",
        "ml_overrides": """key TEXT PRIMARY KEY,kind TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('pinned','suppressed')),
          payload TEXT NOT NULL,username TEXT NOT NULL,updated_at DATETIME NOT NULL""",
        "ml_evidence": """key TEXT PRIMARY KEY,finding_key TEXT NOT NULL REFERENCES ml_findings(key) ON DELETE CASCADE,
          source_kind TEXT NOT NULL,source_id TEXT NOT NULL,source_hash TEXT NOT NULL,group_key TEXT NOT NULL,
          author_id TEXT,start INTEGER NOT NULL,end INTEGER NOT NULL,raw_score FLOAT NOT NULL,
          polarity TEXT NOT NULL,features TEXT NOT NULL,model_version TEXT NOT NULL""",
        "ml_embeddings": """key TEXT PRIMARY KEY,source_kind TEXT NOT NULL,source_id TEXT NOT NULL,
          source_hash TEXT NOT NULL,generation TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
          dimensions INTEGER NOT NULL,vector BLOB NOT NULL,
          bucket0 INTEGER NOT NULL,bucket1 INTEGER NOT NULL,bucket2 INTEGER NOT NULL,bucket3 INTEGER NOT NULL,
          CHECK(length(vector)=dimensions*4)""",
        "ml_budget": """id INTEGER PRIMARY KEY CHECK(id=1),embedding_bytes INTEGER NOT NULL DEFAULT 0,
          evidence_bytes INTEGER NOT NULL DEFAULT 0,active_generation TEXT NOT NULL DEFAULT '',
          staging_generation TEXT,staging_cursor TEXT NOT NULL DEFAULT '',
          staging_reserved_bytes INTEGER NOT NULL DEFAULT 0,staging_bytes INTEGER NOT NULL DEFAULT 0""",
    }
    for name, columns in definitions.items():
        op.execute(f"CREATE TABLE {name} ({columns})")
    op.execute("INSERT INTO ml_budget(id) VALUES(1)")
    for number in range(4):
        op.execute(f"CREATE INDEX ix_ml_embeddings_bucket{number} ON ml_embeddings(generation,dimensions,bucket{number},key)")
    for field in ("src_id", "dst_id", "alias_key", "profile_id"):
        op.execute(f"CREATE INDEX ix_ml_findings_{field} ON ml_findings(kind,json_extract(payload,'$.{field}'))")
    for table, indexes in {
        "ml_findings": ("kind", "canonical_id", "state"), "ml_overrides": ("kind",),
        "ml_sources": ("valid",), "ml_evidence": ("finding_key", "source_kind,source_id", "group_key"),
        "ml_embeddings": ("source_kind,source_id", "generation"),
    }.items():
        for columns in indexes:
            op.execute(f"CREATE INDEX ix_{table}_{columns.replace(',', '_')} ON {table}({columns})")
    for table, column, size, limit in (
        ("ml_embeddings", "embedding_bytes", "length(new.vector)+2048", 4 * 1024**3),
        ("ml_evidence", "evidence_bytes", "length(CAST(new.features AS BLOB))+1024", 2 * 1024**3),
        ("ml_sources", "evidence_bytes", "length(CAST(new.result AS BLOB))+512", 2 * 1024**3),
        ("ml_findings", "evidence_bytes", "length(CAST(new.payload AS BLOB))+1536", 2 * 1024**3),
    ):
        old_size = size.replace("new.", "old.")
        for action, delta in (("INSERT", size), ("UPDATE", f"({size})-({old_size})")):
            _trigger(f"{table}_budget_{action.lower()}", action, table,
                     f"UPDATE ml_budget SET {column}={column}+({delta}) WHERE id=1; "
                     f"SELECT CASE WHEN (SELECT {column} FROM ml_budget WHERE id=1)>{limit} "
                     "THEN RAISE(ABORT,'ML derived storage quota reached') END;")
        _trigger(f"{table}_budget_delete", "DELETE", table,
                 f"UPDATE ml_budget SET {column}={column}-({old_size}) WHERE id=1;")


def _invalidation_triggers():
    for kind, table, columns in (("item", "knowledge_items", ITEM_COLUMNS), ("passage", "document_passages", PASSAGE_COLUMNS)):
        for action, row in (("UPDATE OF " + ",".join(columns), "new"), ("DELETE", "old")):
            operation = "update" if row == "new" else "delete"
            body = f"UPDATE ml_sources SET valid=0 WHERE kind='{kind}' AND id={row}.id;"
            body += f"UPDATE ml_findings SET state='stale' WHERE key IN (SELECT finding_key FROM ml_evidence WHERE source_kind='{kind}' AND source_id={row}.id);"
            if kind == "item":
                body += f"UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id=old.author_profile_id;"
                body += _enqueue("profile", "old.author_profile_id")
                if row == "new":
                    body += "UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id=new.author_profile_id;"
                    body += _enqueue("profile", "new.author_profile_id")
            changed = "WHEN " + " OR ".join(f"new.{column} IS NOT old.{column}" for column in columns) if row == "new" else ""
            _trigger(f"ml_invalidate_{kind}_{operation}", action, table, body, changed)
    for action, row in (("INSERT", "new"), ("DELETE", "old")):
        _trigger(f"ml_invalidate_outcome_{action.lower()}", action, "impact_events",
                 f"UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id={row}.beneficiary_profile_id;")
    _trigger("ml_invalidate_profile_update", "UPDATE OF account_id", "profiles",
             "UPDATE ml_sources SET valid=0 WHERE kind='profile' AND (id=new.id OR id IN "
             "(SELECT beneficiary_profile_id FROM impact_events WHERE actor_profile_id=new.id));"
             + _enqueue_beneficiaries("new.id"),
             "WHEN new.account_id IS NOT old.account_id")
    _trigger("ml_invalidate_account_delete", "DELETE", "accounts",
             "UPDATE ml_sources SET valid=0 WHERE kind='profile' AND (id IN (SELECT id FROM profiles WHERE account_id=old.id) "
             "OR id IN (SELECT beneficiary_profile_id FROM impact_events WHERE actor_profile_id IN (SELECT id FROM profiles WHERE account_id=old.id)));"
             + _enqueue_beneficiaries("(SELECT id FROM profiles WHERE account_id=old.id)"))


def _enqueue_beneficiaries(actor):
    return f"""INSERT INTO ml_jobs(source_kind,source_id,available_at,created_at,updated_at)
      SELECT DISTINCT 'profile',beneficiary_profile_id,{NOW},{NOW},{NOW} FROM impact_events
      WHERE actor_profile_id IN ({actor})
      ON CONFLICT(source_kind,source_id) DO UPDATE SET generation=ml_jobs.generation+1,
        priority=0,available_at={NOW},attempts=0,error=NULL,updated_at={NOW};"""
