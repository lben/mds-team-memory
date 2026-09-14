"""Rebuild retained identities across forward and backward policy transitions."""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _replay():
    # Route policy versions are independent of the inference model version.
    # Both directions need source replay, even when worker startup sees the
    # same model generation. Keep the existing bounded backfill mechanism.
    op.execute("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1 WHERE id=1""")


def upgrade():
    _replay()


def downgrade():
    _replay()
