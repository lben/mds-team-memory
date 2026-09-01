"""Remove three columns nothing ever wrote or read.

`knowledge_items.title` was NULL for every row ever created — no constructor
sets it — yet five call sites concatenated it defensively and it held a slot in
the search index. `relationships.evidence` was written once and read nowhere.
`documents.status` was never anything but its default, and the UI renders "TEXT
EXTRACTED" unconditionally.

The FTS triggers from 0001 name `title`, so they have to go with it: dropping
the column without recreating them would make every insert fail.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TRIGGERS = ("knowledge_items_ai", "knowledge_items_ad", "knowledge_items_au")


def _items_fts(columns: str, triggers: list[str]) -> None:
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS items_fts")
    op.execute(
        f"CREATE VIRTUAL TABLE items_fts USING fts5({columns}, content='knowledge_items', "
        "content_rowid='rowid', tokenize=\"porter unicode61\")"
    )
    for trigger in triggers:
        op.execute(trigger)
    op.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")


BODY_ONLY = [
    """CREATE TRIGGER knowledge_items_ai AFTER INSERT ON knowledge_items BEGIN
         INSERT INTO items_fts(rowid, body) VALUES (new.rowid, new.body);
       END""",
    """CREATE TRIGGER knowledge_items_ad AFTER DELETE ON knowledge_items BEGIN
         INSERT INTO items_fts(items_fts, rowid, body) VALUES ('delete', old.rowid, old.body);
       END""",
    """CREATE TRIGGER knowledge_items_au AFTER UPDATE ON knowledge_items BEGIN
         INSERT INTO items_fts(items_fts, rowid, body) VALUES ('delete', old.rowid, old.body);
         INSERT INTO items_fts(rowid, body) VALUES (new.rowid, new.body);
       END""",
]

WITH_TITLE = [
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
]


def upgrade() -> None:
    # Order matters: SQLite has no DROP COLUMN, so batch_alter_table recreates
    # the table — which silently takes its triggers with it. Rebuild the index
    # and its triggers afterwards, against the table that will actually exist.
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    with op.batch_alter_table("knowledge_items") as batch:
        batch.drop_column("title")
    with op.batch_alter_table("relationships") as batch:
        batch.drop_column("evidence")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("status")
    _items_fts("body", BODY_ONLY)


def downgrade() -> None:
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("status", sa.String(12), server_default="extracted"))
    with op.batch_alter_table("relationships") as batch:
        batch.add_column(sa.Column("evidence", sa.Text()))
    with op.batch_alter_table("knowledge_items") as batch:
        batch.add_column(sa.Column("title", sa.String(300)))
    _items_fts("body, title", WITH_TITLE)
