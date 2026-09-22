"""Version-bound explicit topical feedback; generic impact is never backfilled."""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

NOW = "CAST(strftime('%s','now') AS REAL)"


def _invalidate(profiles):
    return f"""
      UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id IN ({profiles});
      INSERT INTO ml_jobs(source_kind,source_id,available_at,created_at,updated_at)
      SELECT DISTINCT 'profile',id,{NOW},{NOW},{NOW} FROM ({profiles}) WHERE id IS NOT NULL
      ON CONFLICT(source_kind,source_id) DO UPDATE SET generation=ml_jobs.generation+1,
        available_at={NOW},priority=0,attempts=0,error=NULL,updated_at={NOW};
      UPDATE ml_state SET revision=revision+1 WHERE id=1;
    """


def _trigger(name, action, table, body, when=""):
    op.execute(f"CREATE TRIGGER tf_{name} {action} ON {table} {when} BEGIN {body} END")


def upgrade():
    # Revision triggers issue nested metadata-only updates. Reindexing those
    # with the outer update's new body would delete text not yet in FTS and
    # can corrupt the external-content index. Only an actual body edit owns
    # its old/new index entries.
    op.execute("DROP TRIGGER knowledge_items_au")
    op.execute("""CREATE TRIGGER knowledge_items_au AFTER UPDATE OF body ON knowledge_items
      WHEN new.body IS NOT old.body BEGIN
        INSERT INTO items_fts(items_fts,rowid,body) VALUES ('delete',old.rowid,old.body);
        INSERT INTO items_fts(rowid,body) VALUES (new.rowid,new.body);
      END""")
    op.add_column("knowledge_items", sa.Column("evidence_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("knowledge_items", sa.Column("acceptance_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("profiles", sa.Column("account_binding_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("concepts", sa.Column("credit_identity_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("concepts", sa.Column("credit_identity_key", sa.String(120), nullable=False, server_default=""))
    op.execute("""UPDATE concepts SET credit_identity_key=COALESCE(
      (SELECT term FROM concept_terms WHERE concept_id=concepts.id AND is_canonical=1 LIMIT 1),'')""")
    op.execute("""CREATE TABLE topic_confirmations (
      id VARCHAR(32) PRIMARY KEY,
      kind VARCHAR(16) NOT NULL CHECK(kind IN ('helped','accepted')),
      actor_account_id VARCHAR(32) NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
      actor_profile_id VARCHAR(32) NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
      actor_binding_revision INTEGER NOT NULL,
      beneficiary_account_id VARCHAR(32) NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
      beneficiary_profile_id VARCHAR(32) NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
      beneficiary_binding_revision INTEGER NOT NULL,
      item_id VARCHAR(32) NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
      item_evidence_revision INTEGER NOT NULL,
      question_id VARCHAR(32) REFERENCES knowledge_items(id) ON DELETE CASCADE,
      question_evidence_revision INTEGER,
      acceptance_revision INTEGER,
      asker_profile_id VARCHAR(32) REFERENCES profiles(id) ON DELETE CASCADE,
      asker_binding_revision INTEGER,
      concept_id VARCHAR(32) NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
      concept_identity_revision INTEGER NOT NULL,
      contract_version VARCHAR(40) NOT NULL,
      state VARCHAR(16) NOT NULL CHECK(state IN ('current','revoked','superseded')),
      context_token VARCHAR(64) NOT NULL,
      created_at DATETIME NOT NULL,
      state_changed_at DATETIME NOT NULL,
      CHECK(actor_account_id != beneficiary_account_id),
      CHECK(kind != 'accepted' OR (question_id IS NOT NULL AND acceptance_revision IS NOT NULL
        AND asker_profile_id IS NOT NULL AND asker_binding_revision IS NOT NULL)),
      CHECK((question_id IS NULL) = (question_evidence_revision IS NULL))
    )""")
    for column in ("beneficiary_profile_id", "actor_profile_id", "asker_profile_id", "actor_account_id",
                   "beneficiary_account_id", "item_id", "question_id", "concept_id", "state"):
        op.execute(f"CREATE INDEX ix_topic_confirmations_{column} ON topic_confirmations({column})")
    op.execute("""CREATE UNIQUE INDEX uq_topic_confirmations_current ON topic_confirmations
      (actor_account_id,item_id,concept_id,kind) WHERE state='current'""")

    for action, row in (("AFTER INSERT", "new"), ("AFTER DELETE", "old"), ("AFTER UPDATE", "new")):
        _trigger("confirmation_" + action.split()[-1].lower(), action, "topic_confirmations",
                 _invalidate(f"SELECT {row}.beneficiary_profile_id AS id"))
    fields = ("id", "created_at", "kind", "actor_account_id", "actor_profile_id", "actor_binding_revision", "beneficiary_account_id",
              "beneficiary_profile_id", "beneficiary_binding_revision", "item_id", "item_evidence_revision",
              "question_id", "question_evidence_revision", "acceptance_revision", "asker_profile_id",
              "asker_binding_revision", "concept_id", "concept_identity_revision", "contract_version", "context_token")
    _trigger("confirmation_immutable", "BEFORE UPDATE", "topic_confirmations",
             "SELECT RAISE(ABORT,'Topic confirmation assertions are immutable');",
             "WHEN " + " OR ".join(f"new.{c} IS NOT old.{c}" for c in fields))
    _trigger("confirmation_no_revival", "BEFORE UPDATE OF state", "topic_confirmations",
             "SELECT RAISE(ABORT,'Withdrawn topic confirmation requires a new assertion');",
             "WHEN old.state!='current' AND new.state='current'")

    semantic = ("body", "kind", "visibility", "author_profile_id", "parent_id", "source_document_id",
                "source_passage_id", "source_item_id", "correction_state")
    _trigger("item_version", "AFTER UPDATE OF " + ",".join(semantic), "knowledge_items",
             "UPDATE knowledge_items SET evidence_revision=evidence_revision+1 WHERE id=new.id;",
             "WHEN " + " OR ".join(f"new.{c} IS NOT old.{c}" for c in semantic))
    _trigger("acceptance_version", "AFTER UPDATE OF accepted_answer_id,question_status", "knowledge_items",
             "UPDATE knowledge_items SET acceptance_revision=acceptance_revision+1 WHERE id=new.id;",
             "WHEN new.accepted_answer_id IS NOT old.accepted_answer_id OR "
             "(new.question_status IS NOT old.question_status AND (new.question_status='resolved' OR old.question_status='resolved'))")
    # An adopted correction changes the displayed authoritative context.
    _trigger("correction_context", "AFTER UPDATE OF correction_state,body,parent_id", "knowledge_items",
             """UPDATE knowledge_items SET evidence_revision=evidence_revision+1
                WHERE id IN (old.parent_id,new.parent_id);""",
             "WHEN (old.correction_state='adopted' OR new.correction_state='adopted') AND "
             "(old.correction_state IS NOT new.correction_state OR old.body IS NOT new.body OR old.parent_id IS NOT new.parent_id)")
    _trigger("correction_insert", "AFTER INSERT", "knowledge_items",
             "UPDATE knowledge_items SET evidence_revision=evidence_revision+1 WHERE id=new.parent_id;",
             "WHEN new.kind='correction' AND new.correction_state='adopted'")

    # Originality is computed among this beneficiary's contributions. A group
    # change invalidates its own author; changes to other members fire their
    # own triggers. Only question context needs the inverse beneficiary lookup.
    affected = """SELECT old.author_profile_id AS id UNION SELECT new.author_profile_id UNION
      SELECT beneficiary_profile_id FROM topic_confirmations WHERE item_id=new.id OR question_id=new.id"""
    _trigger("item_dependencies", "AFTER UPDATE OF evidence_revision,acceptance_revision,group_id,normalized_hash", "knowledge_items",
             _invalidate(affected), "WHEN new.evidence_revision IS NOT old.evidence_revision OR "
             "new.acceptance_revision IS NOT old.acceptance_revision OR new.group_id IS NOT old.group_id OR "
             "new.normalized_hash IS NOT old.normalized_hash")
    _trigger("item_delete", "BEFORE DELETE", "knowledge_items",
             _invalidate("SELECT old.author_profile_id AS id UNION SELECT beneficiary_profile_id FROM topic_confirmations "
                         "WHERE item_id=old.id OR question_id=old.id") + """
             UPDATE knowledge_items SET accepted_answer_id=NULL,question_status=CASE WHEN EXISTS(
               SELECT 1 FROM knowledge_items a WHERE a.parent_id=knowledge_items.id AND a.kind='answer' AND a.id!=old.id)
               THEN 'answered' ELSE 'open' END WHERE accepted_answer_id=old.id;
             UPDATE knowledge_items SET evidence_revision=evidence_revision+1
               WHERE id=old.parent_id AND old.correction_state='adopted';""")

    _trigger("profile_version", "AFTER UPDATE OF account_id", "profiles",
             "UPDATE profiles SET account_binding_revision=account_binding_revision+1 WHERE id=new.id;",
             "WHEN new.account_id IS NOT old.account_id")
    for action, row, suffix in (("AFTER UPDATE OF account_binding_revision", "new", "update"), ("BEFORE DELETE", "old", "delete")):
        _trigger("profile_" + suffix, action, "profiles", _invalidate(
            f"SELECT {row}.id AS id UNION SELECT beneficiary_profile_id FROM topic_confirmations WHERE "
            f"actor_profile_id={row}.id OR asker_profile_id={row}.id OR beneficiary_profile_id={row}.id"))
    _trigger("account_delete", "BEFORE DELETE", "accounts", _invalidate(
        "SELECT id FROM profiles WHERE account_id=old.id UNION SELECT beneficiary_profile_id FROM topic_confirmations "
        "WHERE actor_account_id=old.id OR beneficiary_account_id=old.id"))

    for action, suffix in (("AFTER INSERT", "insert"), ("AFTER UPDATE OF term,is_canonical,concept_id", "update")):
        _trigger("canonical_" + suffix, action, "concept_terms",
                 """UPDATE concepts SET credit_identity_revision=credit_identity_revision+1,credit_identity_key=new.term
                    WHERE id=new.concept_id AND credit_identity_key IS NOT new.term;""", "WHEN new.is_canonical=1")
    _trigger("canonical_delete", "BEFORE DELETE", "concept_terms",
             "UPDATE concepts SET credit_identity_revision=credit_identity_revision+1,credit_identity_key='' WHERE id=old.concept_id;",
             "WHEN old.is_canonical=1")
    _trigger("canonical_depart", "BEFORE UPDATE OF is_canonical,concept_id", "concept_terms",
             "UPDATE concepts SET credit_identity_revision=credit_identity_revision+1,credit_identity_key='' WHERE id=old.concept_id;",
             "WHEN old.is_canonical=1 AND (new.is_canonical!=1 OR old.concept_id IS NOT new.concept_id)")
    for action, row, suffix in (("AFTER UPDATE OF credit_identity_revision", "new", "update"), ("BEFORE DELETE", "old", "delete")):
        _trigger("concept_" + suffix, action, "concepts", _invalidate(
            f"SELECT beneficiary_profile_id AS id FROM topic_confirmations WHERE concept_id={row}.id"))
    # Suppression is a read gate as well; enqueue directly confirmed dependents
    # even though their authority does not depend on an alias mention route.
    for action, row, suffix in (("AFTER INSERT", "new", "insert"), ("AFTER UPDATE", "new", "update"), ("AFTER DELETE", "old", "delete")):
        _trigger("override_" + suffix, action, "ml_overrides", _invalidate(
            "SELECT beneficiary_profile_id AS id FROM topic_confirmations WHERE concept_id IN "
            f"(SELECT canonical_id FROM ml_findings WHERE key={row}.key AND kind='concept')"), f"WHEN {row}.kind='concept'")

    # Withdraw the generic-event projection before any new worker starts.
    op.execute("""UPDATE ml_overrides SET payload=json_set(payload,
      '$.account_id',(SELECT account_id FROM profiles WHERE id=json_extract(ml_overrides.payload,'$.profile_id')),
      '$.account_binding_revision',(SELECT account_binding_revision FROM profiles WHERE id=json_extract(ml_overrides.payload,'$.profile_id')))
      WHERE kind='expertise' AND mode='pinned'""")
    op.execute("UPDATE ml_sources SET valid=0 WHERE kind='profile'")
    # A stale worker may outlive its controller. It cannot certify the old
    # generic-vote projection in this database, even using a legacy UPSERT.
    for action, suffix in (("AFTER INSERT", "insert"), ("AFTER UPDATE OF kind,valid,result", "update")):
        _trigger("profile_contract_" + suffix, action, "ml_sources",
                 "UPDATE ml_sources SET valid=0 WHERE kind=new.kind AND id=new.id;",
                 "WHEN new.kind='profile' AND new.valid=1 AND COALESCE(json_extract("
                 "CASE WHEN json_valid(new.result) THEN new.result ELSE '{}' END,"
                 "'$.topic_feedback_contract'),'') != 'explicit-topic-feedback-v1'")
    op.execute("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1,revision=revision+1 WHERE id=1""")


def downgrade():
    # Old code would project every historical generic event into all its tags.
    # Keeping the additive schema and requiring explicit operator migration is
    # safer than either destroying confirmations or silently restoring that bug.
    raise RuntimeError("0015 stores explicit human topic confirmations. Automatic downgrade is disabled; export/preserve confirmations and use a reviewed migration with expertise held.")
