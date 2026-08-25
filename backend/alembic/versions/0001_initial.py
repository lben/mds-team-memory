"""Initial schema with FTS5 search tables.

Revision ID: 0001
Revises:
Create Date: 2026-08-25
"""

from alembic import op

from app.db import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

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


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    for ddl in FTS_DDL:
        op.execute(ddl)


def downgrade() -> None:
    for name in ("items_fts", "passages_fts"):
        op.execute(f"DROP TABLE IF EXISTS {name}")
    for trigger in (
        "knowledge_items_ai",
        "knowledge_items_ad",
        "knowledge_items_au",
        "document_passages_ai",
        "document_passages_ad",
        "document_passages_au",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    Base.metadata.drop_all(bind=op.get_bind())
