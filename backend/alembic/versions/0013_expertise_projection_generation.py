"""Withdraw old expertise projections before generation replay in either direction."""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _invalidate():
    # Pins remain authoritative independently of automatic Source validity.
    # Invalidating now protects rollback readers that do not know the new
    # projection contract; the existing paged backfill rebuilds profiles later.
    op.execute("UPDATE ml_sources SET valid=0 WHERE kind='profile'")
    op.execute("""UPDATE ml_state SET backfill_kind='item',backfill_cursor='',
      backfill_generation=backfill_generation+1 WHERE id=1""")


def upgrade():
    _invalidate()


def downgrade():
    _invalidate()
