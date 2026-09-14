"""Invalidate conditional expertise when definition coverage rules change."""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    # Contributions can have been projected into expertise before their own
    # source replay. Match the existing coverage trigger's profile scope.
    profiles = """SELECT profile.id FROM ml_sources profile
      JOIN profiles current ON current.id=profile.id
      JOIN accounts account ON account.id=current.account_id
      WHERE profile.kind='profile' AND EXISTS (SELECT 1 FROM ml_evidence
        WHERE json_type(features,'$.identity_routes')='array')"""
    now = "CAST(strftime('%s', 'now') AS REAL)"
    op.execute(f"""CREATE TRIGGER ml_identity_coverage_revision AFTER UPDATE OF result ON ml_sources
      WHEN new.kind IN ('item','passage') AND new.valid=1 AND (
        json_extract(old.result,'$.conflict_coverage_revision')
          IS NOT json_extract(new.result,'$.conflict_coverage_revision')
        OR json_type(old.result,'$.conflict_definitions')
          IS NOT json_type(new.result,'$.conflict_definitions'))
      BEGIN
        UPDATE ml_sources SET valid=0 WHERE kind='profile' AND id IN ({profiles});
        INSERT INTO ml_jobs(source_kind,source_id,available_at,created_at,updated_at)
        SELECT DISTINCT 'profile',id,{now},{now},{now} FROM ({profiles}) WHERE id IS NOT NULL
        ON CONFLICT(source_kind,source_id) DO UPDATE SET generation=ml_jobs.generation+1,
          priority=0,available_at={now},attempts=0,error=NULL,updated_at={now};
      END""")


def downgrade():
    op.execute("DROP TRIGGER ml_identity_coverage_revision")
