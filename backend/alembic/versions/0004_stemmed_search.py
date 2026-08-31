"""Stem the full-text index so word forms match each other.

Searching "emulate" should find content that says "emulation". Expanding the
query cannot do that — the index stores literal words, and we do not know which
forms the content used. Stemming the index itself is the fix, and SQLite's FTS5
ships a porter tokenizer, so no dependency is added.

The tables are external-content indexes, so they are recreated and rebuilt from
knowledge_items and document_passages; no content is stored twice and nothing
is lost. The existing triggers keep working unchanged.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-31
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = {
    "items_fts": (
        "body, title, content='knowledge_items', content_rowid='rowid'",
        "knowledge_items",
    ),
    "passages_fts": (
        "text, content='document_passages', content_rowid='rowid'",
        "document_passages",
    ),
}


def _recreate(tokenize: str) -> None:
    for name, (columns, _source) in TABLES.items():
        op.execute(f"DROP TABLE IF EXISTS {name}")
        op.execute(f"CREATE VIRTUAL TABLE {name} USING fts5({columns}{tokenize})")
        op.execute(f"INSERT INTO {name}({name}) VALUES('rebuild')")


def upgrade() -> None:
    _recreate(", tokenize=\"porter unicode61\"")


def downgrade() -> None:
    _recreate("")
