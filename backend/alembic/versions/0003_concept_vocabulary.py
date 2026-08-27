"""One vocabulary table, and separately keyed tag tables.

Folds concepts.name and concept_aliases into a single concept_terms table where
every searchable word is unique across all concepts, and replaces the
polymorphic item_concepts table with item_concepts + passage_concepts carrying
real foreign keys.

Existing data may contain collisions this schema forbids (a name reused as
another concept's alias, or two concepts differing only by case). The first
owner keeps the word; a concept whose own name is taken is given a
disambiguated name so it survives for an admin to merge or delete.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _uid(connection) -> str:
    return connection.execute(sa.text("SELECT lower(hex(randomblob(16)))")).scalar()


def upgrade() -> None:
    connection = op.get_bind()

    op.create_table(
        "concept_terms",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "concept_id",
            sa.String(32),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term", sa.String(120), nullable=False, unique=True),
        sa.Column("display", sa.String(120), nullable=False),
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_concept_terms_concept_id", "concept_terms", ["concept_id"])

    taken: set[str] = set()
    concepts = connection.execute(sa.text("SELECT id, name FROM concepts ORDER BY name")).fetchall()
    for concept_id, name in concepts:
        term = " ".join((name or "").lower().split())
        display = (name or "").strip()
        if not term or term in taken:
            # The word belongs to an earlier concept; keep this one under a
            # disambiguated name so no data is lost.
            display = f"{display or 'concept'} ({concept_id[:4]})"
            term = " ".join(display.lower().split())
        taken.add(term)
        connection.execute(
            sa.text(
                "INSERT INTO concept_terms (id, concept_id, term, display, is_canonical) "
                "VALUES (:i, :c, :t, :d, 1)"
            ),
            {"i": _uid(connection), "c": concept_id, "t": term, "d": display},
        )

    aliases = connection.execute(
        sa.text("SELECT concept_id, alias FROM concept_aliases ORDER BY alias")
    ).fetchall()
    for concept_id, alias in aliases:
        term = " ".join((alias or "").lower().split())
        if not term or term in taken:
            continue  # already owned, by this concept or an earlier one
        taken.add(term)
        connection.execute(
            sa.text(
                "INSERT INTO concept_terms (id, concept_id, term, display, is_canonical) "
                "VALUES (:i, :c, :t, :d, 0)"
            ),
            {"i": _uid(connection), "c": concept_id, "t": term, "d": alias.strip()},
        )

    op.create_table(
        "passage_concepts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "passage_id",
            sa.String(32),
            sa.ForeignKey("document_passages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "concept_id",
            sa.String(32),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("passage_id", "concept_id"),
    )
    # Only rows whose passage still exists, so the new foreign key holds.
    connection.execute(
        sa.text(
            "INSERT INTO passage_concepts (id, passage_id, concept_id) "
            "SELECT ic.id, ic.subject_id, ic.concept_id FROM item_concepts ic "
            "JOIN document_passages p ON p.id = ic.subject_id "
            "WHERE ic.subject_kind = 'passage'"
        )
    )

    op.rename_table("item_concepts", "item_concepts_old")
    op.create_table(
        "item_concepts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "item_id",
            sa.String(32),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "concept_id",
            sa.String(32),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("item_id", "concept_id"),
    )
    connection.execute(
        sa.text(
            "INSERT INTO item_concepts (id, item_id, concept_id) "
            "SELECT ic.id, ic.subject_id, ic.concept_id FROM item_concepts_old ic "
            "JOIN knowledge_items k ON k.id = ic.subject_id "
            "WHERE ic.subject_kind = 'item'"
        )
    )
    op.drop_table("item_concepts_old")
    op.create_index("ix_item_concepts_item_id", "item_concepts", ["item_id"])
    op.create_index("ix_item_concepts_concept_id", "item_concepts", ["concept_id"])
    op.create_index("ix_passage_concepts_passage_id", "passage_concepts", ["passage_id"])
    op.create_index("ix_passage_concepts_concept_id", "passage_concepts", ["concept_id"])

    op.drop_table("concept_aliases")
    with op.batch_alter_table("concepts") as batch:
        batch.drop_column("name")
    op.add_column("concepts", sa.Column("created_at", sa.DateTime()))
    connection.execute(sa.text("UPDATE concepts SET created_at = CURRENT_TIMESTAMP"))


def downgrade() -> None:
    connection = op.get_bind()
    op.add_column("concepts", sa.Column("name", sa.String(120)))
    connection.execute(
        sa.text(
            "UPDATE concepts SET name = ("
            "  SELECT display FROM concept_terms t "
            "  WHERE t.concept_id = concepts.id AND t.is_canonical = 1 LIMIT 1)"
        )
    )
    op.create_table(
        "concept_aliases",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "concept_id",
            sa.String(32),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(120), nullable=False, unique=True),
    )
    connection.execute(
        sa.text(
            "INSERT INTO concept_aliases (id, concept_id, alias) "
            "SELECT id, concept_id, term FROM concept_terms WHERE is_canonical = 0"
        )
    )

    op.rename_table("item_concepts", "item_concepts_new")
    op.create_table(
        "item_concepts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("subject_kind", sa.String(12), nullable=False),
        sa.Column("subject_id", sa.String(32), nullable=False),
        sa.Column(
            "concept_id",
            sa.String(32),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("subject_kind", "subject_id", "concept_id"),
    )
    connection.execute(
        sa.text(
            "INSERT INTO item_concepts (id, subject_kind, subject_id, concept_id) "
            "SELECT id, 'item', item_id, concept_id FROM item_concepts_new"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO item_concepts (id, subject_kind, subject_id, concept_id) "
            "SELECT id, 'passage', passage_id, concept_id FROM passage_concepts"
        )
    )
    op.drop_table("item_concepts_new")
    op.drop_table("passage_concepts")
    op.drop_table("concept_terms")
    with op.batch_alter_table("concepts") as batch:
        batch.drop_column("created_at")
    op.create_index("ix_item_concepts_subject_id", "item_concepts", ["subject_id"])
    op.create_index("ix_item_concepts_concept_id", "item_concepts", ["concept_id"])
