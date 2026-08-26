"""Relationship types vocabulary and review state on relationships.

Rebuilds the relationships table so rel_type/confidence become a foreign key to
the new relationship_types table plus a review state. Existing rows are
preserved.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

RELATED_TO_ID = "00000000000000000000000000000001"
CORROBORATES_ID = "00000000000000000000000000000002"


def upgrade() -> None:
    op.create_table(
        "relationship_types",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.execute(
        "INSERT INTO relationship_types (id, name, is_builtin, created_at) VALUES "
        f"('{RELATED_TO_ID}', 'related to', 1, CURRENT_TIMESTAMP), "
        f"('{CORROBORATES_ID}', 'corroborates', 1, CURRENT_TIMESTAMP)"
    )

    # SQLite cannot drop columns that participate in constraints, so rebuild the
    # table explicitly and copy the existing rows across.
    op.rename_table("relationships", "relationships_old")
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("src_kind", sa.String(12), nullable=False),
        sa.Column("src_id", sa.String(32), nullable=False),
        sa.Column("dst_kind", sa.String(12), nullable=False),
        sa.Column("dst_id", sa.String(32), nullable=False),
        sa.Column(
            "relationship_type_id",
            sa.String(32),
            sa.ForeignKey("relationship_types.id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(12), nullable=False, server_default="suggested"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.String(80)),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("src_kind", "src_id", "dst_kind", "dst_id"),
    )
    op.execute(
        "INSERT INTO relationships "
        "(id, src_kind, src_id, dst_kind, dst_id, relationship_type_id, state, "
        " occurrence_count, evidence, created_at) "
        "SELECT id, src_kind, src_id, dst_kind, dst_id, "
        f"  CASE WHEN rel_type = 'corroborates' THEN '{CORROBORATES_ID}' ELSE '{RELATED_TO_ID}' END, "
        "  CASE WHEN confidence = 'confirmed' THEN 'confirmed' ELSE 'suggested' END, "
        "  0, evidence, created_at "
        "FROM relationships_old"
    )
    op.drop_table("relationships_old")
    op.create_index("ix_relationships_src_id", "relationships", ["src_id"])
    op.create_index("ix_relationships_dst_id", "relationships", ["dst_id"])
    op.create_index("ix_relationships_state", "relationships", ["state"])


def downgrade() -> None:
    op.rename_table("relationships", "relationships_new")
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("src_kind", sa.String(12), nullable=False),
        sa.Column("src_id", sa.String(32), nullable=False, index=True),
        sa.Column("rel_type", sa.String(24), nullable=False),
        sa.Column("dst_kind", sa.String(12), nullable=False),
        sa.Column("dst_id", sa.String(32), nullable=False, index=True),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(12), nullable=False, server_default="inferred"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("src_kind", "src_id", "rel_type", "dst_kind", "dst_id"),
    )
    op.execute(
        "INSERT INTO relationships "
        "(id, src_kind, src_id, rel_type, dst_kind, dst_id, evidence, confidence, created_at) "
        "SELECT r.id, r.src_kind, r.src_id, t.name, r.dst_kind, r.dst_id, r.evidence, "
        "  CASE WHEN r.state = 'confirmed' THEN 'confirmed' ELSE 'inferred' END, r.created_at "
        "FROM relationships_new r JOIN relationship_types t ON t.id = r.relationship_type_id"
    )
    op.drop_table("relationships_new")
    op.drop_table("relationship_types")
