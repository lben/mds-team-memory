"""Index bounded readiness checks for source-grounded alias definitions."""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE INDEX ix_ml_sources_definition_coverage ON ml_sources
      (valid,COALESCE(json_extract(result,'$.definitions_indexed'),0))
      WHERE kind IN ('item','passage')""")


def downgrade():
    op.execute("DROP INDEX ix_ml_sources_definition_coverage")
