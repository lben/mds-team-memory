"""Initial schema with FTS5 search tables.

The tables are written out explicitly rather than generated from the live models
so that this revision keeps describing the schema as it was at this point in
history, and later revisions can migrate away from it.

Revision ID: 0001
Revises:
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "revisions",
    "relationships",
    "notifications",
    "impact_events",
    "expertise_mappings",
    "item_concepts",
    "concept_aliases",
    "concepts",
    "document_passages",
    "documents",
    "scratchpads",
    "knowledge_items",
    "admin_sessions",
    "admin_users",
    "profiles",
]

FTS_DDL = [
    "CREATE VIRTUAL TABLE items_fts USING fts5(body, title, content='knowledge_items', content_rowid='rowid')",
    """CREATE TRIGGER knowledge_items_ai AFTER INSERT ON knowledge_items BEGIN
         INSERT INTO items_fts(rowid, body, title) VALUES (new.rowid, new.body, coalesce(new.title,''));
       END""",
    """CREATE TRIGGER knowledge_items_ad AFTER DELETE ON knowledge_items BEGIN
         INSERT INTO items_fts(items_fts, rowid, body, title) VALUES ('delete', old.rowid, old.body, coalesce(old.title,''));
       END""",
    """CREATE TRIGGER knowledge_items_au AFTER UPDATE ON knowledge_items BEGIN
         INSERT INTO items_fts(items_fts, rowid, body, title) VALUES ('delete', old.rowid, old.body, coalesce(old.title,''));
         INSERT INTO items_fts(rowid, body, title) VALUES (new.rowid, new.body, coalesce(new.title,''));
       END""",
    "CREATE VIRTUAL TABLE passages_fts USING fts5(text, content='document_passages', content_rowid='rowid')",
    """CREATE TRIGGER document_passages_ai AFTER INSERT ON document_passages BEGIN
         INSERT INTO passages_fts(rowid, text) VALUES (new.rowid, new.text);
       END""",
    """CREATE TRIGGER document_passages_ad AFTER DELETE ON document_passages BEGIN
         INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
       END""",
    """CREATE TRIGGER document_passages_au AFTER UPDATE ON document_passages BEGIN
         INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
         INSERT INTO passages_fts(rowid, text) VALUES (new.rowid, new.text);
       END""",
]

TRIGGERS = [
    "knowledge_items_ai",
    "knowledge_items_ad",
    "knowledge_items_au",
    "document_passages_ai",
    "document_passages_ad",
    "document_passages_au",
]


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80)),
        sa.Column("account_id", sa.String(32)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_profiles_token_hash", "profiles", ["token_hash"])

    op.create_table(
        "admin_users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "admin_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "admin_user_id",
            sa.String(32),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("uploader_profile_id", sa.String(32), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="extracted"),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "document_passages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(32),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", sa.String(120), nullable=False),
    )
    op.create_index("ix_document_passages_document_id", "document_passages", ["document_id"])

    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(8), nullable=False, server_default="team"),
        sa.Column("author_profile_id", sa.String(32), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("parent_id", sa.String(32), sa.ForeignKey("knowledge_items.id")),
        sa.Column("source_document_id", sa.String(32), sa.ForeignKey("documents.id")),
        sa.Column("source_passage_id", sa.String(32), sa.ForeignKey("document_passages.id")),
        sa.Column("source_item_id", sa.String(32)),
        sa.Column("group_id", sa.String(32)),
        sa.Column("normalized_hash", sa.String(64)),
        sa.Column("question_status", sa.String(12)),
        sa.Column("accepted_answer_id", sa.String(32)),
        sa.Column("correction_state", sa.String(12)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("kind", "visibility", "author_profile_id", "parent_id", "group_id", "normalized_hash", "created_at"):
        op.create_index(f"ix_knowledge_items_{column}", "knowledge_items", [column])

    op.create_table(
        "scratchpads",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("name", sa.String(120)),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scratchpads_profile_id", "scratchpads", ["profile_id"])

    op.create_table(
        "concepts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
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
    op.create_index("ix_concept_aliases_concept_id", "concept_aliases", ["concept_id"])

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
    op.create_index("ix_item_concepts_subject_id", "item_concepts", ["subject_id"])
    op.create_index("ix_item_concepts_concept_id", "item_concepts", ["concept_id"])

    op.create_table(
        "expertise_mappings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column(
            "concept_id",
            sa.String(32),
            sa.ForeignKey("concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("profile_id", "concept_id"),
    )
    op.create_index("ix_expertise_mappings_profile_id", "expertise_mappings", ["profile_id"])
    op.create_index("ix_expertise_mappings_concept_id", "expertise_mappings", ["concept_id"])

    op.create_table(
        "impact_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("actor_profile_id", sa.String(32)),
        sa.Column(
            "beneficiary_profile_id", sa.String(32), sa.ForeignKey("profiles.id"), nullable=False
        ),
        sa.Column("item_id", sa.String(32)),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.String(200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_impact_events_beneficiary", "impact_events", ["beneficiary_profile_id"])
    op.create_index("ix_impact_events_item_id", "impact_events", ["item_id"])
    op.create_index("ix_impact_events_created_at", "impact_events", ["created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("item_id", sa.String(32)),
        sa.Column("dedup_key", sa.String(200), unique=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_notifications_profile_id", "notifications", ["profile_id"])

    op.create_table(
        "relationships",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("src_kind", sa.String(12), nullable=False),
        sa.Column("src_id", sa.String(32), nullable=False),
        sa.Column("rel_type", sa.String(24), nullable=False),
        sa.Column("dst_kind", sa.String(12), nullable=False),
        sa.Column("dst_id", sa.String(32), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(12), nullable=False, server_default="inferred"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("src_kind", "src_id", "rel_type", "dst_kind", "dst_id"),
    )
    op.create_index("ix_relationships_src_id", "relationships", ["src_id"])
    op.create_index("ix_relationships_dst_id", "relationships", ["dst_id"])

    op.create_table(
        "revisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("item_id", sa.String(32), sa.ForeignKey("knowledge_items.id"), nullable=False),
        sa.Column("correction_id", sa.String(32), sa.ForeignKey("knowledge_items.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_revisions_item_id", "revisions", ["item_id"])

    for ddl in FTS_DDL:
        op.execute(ddl)


def downgrade() -> None:
    for trigger in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for name in ("items_fts", "passages_fts"):
        op.execute(f"DROP TABLE IF EXISTS {name}")
    for table in TABLES:
        op.drop_table(table)
