"""Invalidate pre-scope inference and request bounded replay in either direction."""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _invalidate():
    # Keep source/evidence records for diagnostics and explicit manual pins.
    # Old rollback readers have no new guard, so validity must change in SQL.
    op.execute("UPDATE ml_sources SET valid=0 WHERE kind IN ('item','passage','profile')")
    op.execute("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1,revision=revision+1 WHERE id=1""")


def upgrade():
    op.create_index("ix_ml_findings_kind_canonical", "ml_findings", ["kind", "canonical_id"])
    _invalidate()


def downgrade():
    op.drop_index("ix_ml_findings_kind_canonical", table_name="ml_findings")
    _invalidate()
