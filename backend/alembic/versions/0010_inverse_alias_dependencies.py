"""Retain inverse-alias anchor and declaration invalidation."""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _profiles(affected):
    profiles = f"""SELECT profile.id FROM ml_sources profile
      JOIN profiles current ON current.id=profile.id
      JOIN accounts account ON account.id=current.account_id
      WHERE profile.kind='profile' AND EXISTS ({affected})"""
    now = "CAST(strftime('%s', 'now') AS REAL)"
    return f"""UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id IN ({profiles});
      INSERT INTO ml_jobs(source_kind,source_id,available_at,created_at,updated_at)
      SELECT DISTINCT 'profile',id,{now},{now},{now} FROM ({profiles}) WHERE id IS NOT NULL
      ON CONFLICT(source_kind,source_id) DO UPDATE SET generation=ml_jobs.generation+1,
        priority=0,available_at={now},attempts=0,error=NULL,updated_at={now};"""


def _replace_triggers(inverse):
    anchors = """OR EXISTS (SELECT 1 FROM json_each(route.value,'$.anchors') anchor
        WHERE json_extract(anchor.value,'$.source_kind')=old.kind
          AND json_extract(anchor.value,'$.source_id')=old.id)""" if inverse else ""
    affected = f"""SELECT e.finding_key FROM ml_evidence e, json_each(e.features,'$.identity_routes') route
      WHERE json_type(e.features,'$.identity_routes')='array' AND (
        json_extract(route.value,'$.witness_source_kind')=old.kind
          AND json_extract(route.value,'$.witness_source_id')=old.id {anchors})"""
    for action in ("UPDATE", "DELETE"):
        name = "ml_identity_witness_" + action.lower()
        op.execute(f"DROP TRIGGER {name}")
        guard = """WHEN old.valid IS NOT new.valid OR old.content_hash IS NOT new.content_hash
          OR old.model_version IS NOT new.model_version""" if action == "UPDATE" else ""
        op.execute(f"""CREATE TRIGGER {name} AFTER {action} ON ml_sources {guard}
          BEGIN
            UPDATE ml_findings SET state='stale' WHERE key IN ({affected});
            {_profiles(affected)}
            UPDATE ml_state SET backfill_kind='item',backfill_cursor='',backfill_generation=backfill_generation+1
              WHERE id=1 AND EXISTS ({affected});
          END""")
    for action in ("INSERT", "UPDATE", "DELETE"):
        row = "old" if action == "DELETE" else "new"
        extra = f"""OR {row}.key=json_extract(route.value,'$.declaration_key')
          OR ({row}.kind='alias' AND json_extract({row}.payload,'$.alias_key')=json_extract(route.value,'$.published_alias'))""" if inverse else ""
        affected = f"""SELECT e.author_id FROM ml_evidence e, json_each(e.features,'$.identity_routes') route
          WHERE json_type(e.features,'$.identity_routes')='array' AND (
            {row}.key=json_extract(route.value,'$.concept_key')
            OR {row}.key=json_extract(route.value,'$.term_key')
            OR {row}.key=json_extract(route.value,'$.full_term_key')
            OR ({row}.kind='alias' AND json_extract({row}.payload,'$.alias_key')=json_extract(route.value,'$.alias_key'))
            OR ({row}.kind='mention' AND json_extract({row}.payload,'$.source_kind')=e.source_kind
                AND json_extract({row}.payload,'$.source_id')=e.source_id) {extra})"""
        name = "ml_identity_override_" + action.lower()
        op.execute(f"DROP TRIGGER {name}")
        op.execute(f"CREATE TRIGGER {name} AFTER {action} ON ml_overrides BEGIN {_profiles(affected)} END")


def upgrade():
    _replace_triggers(True)
    op.execute("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1 WHERE id=1""")


def downgrade():
    _replace_triggers(False)
