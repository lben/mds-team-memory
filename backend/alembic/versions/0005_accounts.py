"""One identity system: accounts replace admin-only users.

Admin login and contributor identity used to be entirely separate, which is why
an admin's own contributions were credited to a hex code. Both are now accounts;
`is_admin` is the only difference, and a session binds a browser to an account,
which in turn owns the profile every contribution hangs off.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_accounts_is_admin", "accounts", ["is_admin"])
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(32),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )

    # Existing admins become accounts and keep their passwords.
    op.execute(
        "INSERT INTO accounts (id, username, password_hash, is_admin, created_at) "
        "SELECT id, username, password_hash, 1, created_at FROM admin_users"
    )
    # Sessions are not carried over: admins sign in again once, and from then on
    # the session means something different (it decides who you are, not just
    # what you may do).
    op.drop_table("admin_sessions")
    op.drop_table("admin_users")

    with op.batch_alter_table("profiles") as batch:
        batch.add_column(
            sa.Column("claim_locked", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        # A profile that belongs to an account was never a browser's.
        batch.alter_column("token_hash", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.alter_column("token_hash", existing_type=sa.String(64), nullable=False)
        batch.drop_column("claim_locked")
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
    op.execute(
        "INSERT INTO admin_users (id, username, password_hash, created_at) "
        "SELECT id, username, password_hash, created_at FROM accounts WHERE is_admin = 1"
    )
    op.drop_index("ix_accounts_is_admin", table_name="accounts")
    op.drop_table("sessions")
    op.drop_table("accounts")
