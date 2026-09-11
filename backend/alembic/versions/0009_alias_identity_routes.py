"""Invalidate conditional alias identity evidence when its witness changes."""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE INDEX ix_ml_evidence_identity_routes ON ml_evidence(finding_key)
      WHERE json_type(features,'$.identity_routes')='array'""")
    dependent = """SELECT e.finding_key FROM ml_evidence e, json_each(e.features,'$.identity_routes') route
      WHERE json_type(e.features,'$.identity_routes')='array'
        AND json_extract(route.value,'$.witness_source_kind')=old.kind
        AND json_extract(route.value,'$.witness_source_id')=old.id"""
    authors = dependent.replace("SELECT e.finding_key", "SELECT e.author_id")
    profiles = _invalidate_profiles(authors)
    op.execute(f"""CREATE TRIGGER ml_identity_witness_update AFTER UPDATE ON ml_sources
      WHEN old.valid IS NOT new.valid OR old.content_hash IS NOT new.content_hash
        OR old.model_version IS NOT new.model_version
      BEGIN
        UPDATE ml_findings SET state='stale' WHERE key IN ({dependent});
        {profiles}
        UPDATE ml_state SET backfill_kind='item',backfill_cursor='',backfill_generation=backfill_generation+1
          WHERE id=1 AND EXISTS ({dependent});
      END""")
    op.execute(f"""CREATE TRIGGER ml_identity_witness_delete AFTER DELETE ON ml_sources
      BEGIN
        UPDATE ml_findings SET state='stale' WHERE key IN ({dependent});
        {profiles}
        UPDATE ml_state SET backfill_kind='item',backfill_cursor='',backfill_generation=backfill_generation+1
          WHERE id=1 AND EXISTS ({dependent});
      END""")
    for action in ("INSERT", "UPDATE", "DELETE"):
        row = "old" if action == "DELETE" else "new"
        affected_authors = f"""SELECT e.author_id FROM ml_evidence e,
          json_each(e.features,'$.identity_routes') route
          WHERE json_type(e.features,'$.identity_routes')='array' AND (
            {row}.key=json_extract(route.value,'$.concept_key')
            OR {row}.key=json_extract(route.value,'$.term_key')
            OR {row}.key=json_extract(route.value,'$.full_term_key')
            OR ({row}.kind='alias' AND json_extract({row}.payload,'$.alias_key')=json_extract(route.value,'$.alias_key'))
            OR ({row}.kind='mention' AND json_extract({row}.payload,'$.source_kind')=e.source_kind
                AND json_extract({row}.payload,'$.source_id')=e.source_id))"""
        op.execute(f"""CREATE TRIGGER ml_identity_override_{action.lower()} AFTER {action} ON ml_overrides
          BEGIN {_invalidate_profiles(affected_authors)} END""")
    # A pinned concept can outlive its automatic alias. A generation change
    # must hide the alias-dependent expertise before the first replay starts.
    routed_authors = """SELECT author_id FROM ml_evidence
      WHERE json_type(features,'$.identity_routes')='array'"""
    op.execute(f"""CREATE TRIGGER ml_identity_generation_update AFTER UPDATE OF pipeline_version ON ml_state
      WHEN old.pipeline_version IS NOT new.pipeline_version
      BEGIN {_invalidate_profiles(routed_authors)} END""")
    for action in ("INSERT", "UPDATE OF valid,result"):
        operation = "insert" if action == "INSERT" else "update"
        changed = "" if action == "INSERT" else """AND (old.valid IS NOT new.valid
          OR COALESCE(json_extract(old.result,'$.definitions_indexed'),0)
             IS NOT COALESCE(json_extract(new.result,'$.definitions_indexed'),0))"""
        op.execute(f"""CREATE TRIGGER ml_identity_coverage_{operation} AFTER {action} ON ml_sources
          WHEN new.kind IN ('item','passage') AND new.valid=1
            AND COALESCE(json_extract(new.result,'$.definitions_indexed'),0)<2 {changed}
          BEGIN {_invalidate_profiles(routed_authors)} END""")
    # Cached replay retains exact methods on definitions before publication.
    op.execute("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1 WHERE id=1""")


def _invalidate_profiles(affected_routes):
    # Public profile recomputation can precede source replay, so the contributor
    # need not own routed evidence yet. Rebuild the small profile projection set.
    profiles = f"""SELECT profile.id FROM ml_sources profile
      JOIN profiles current ON current.id=profile.id
      JOIN accounts account ON account.id=current.account_id
      WHERE profile.kind='profile' AND EXISTS ({affected_routes})"""
    now = "CAST(strftime('%s', 'now') AS REAL)"
    return f"""UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id IN ({profiles});
      INSERT INTO ml_jobs(source_kind,source_id,available_at,created_at,updated_at)
      SELECT DISTINCT 'profile',id,{now},{now},{now} FROM ({profiles}) WHERE id IS NOT NULL
      ON CONFLICT(source_kind,source_id) DO UPDATE SET generation=ml_jobs.generation+1,
        priority=0,available_at={now},attempts=0,error=NULL,updated_at={now};"""


def downgrade():
    for operation in ("insert", "update"):
        op.execute(f"DROP TRIGGER ml_identity_coverage_{operation}")
    op.execute("DROP TRIGGER ml_identity_generation_update")
    for action in ("insert", "update", "delete"):
        op.execute(f"DROP TRIGGER ml_identity_override_{action}")
    op.execute("DROP TRIGGER ml_identity_witness_delete")
    op.execute("DROP TRIGGER ml_identity_witness_update")
    op.execute("DROP INDEX ix_ml_evidence_identity_routes")
