"""add incognito chats

Revision ID: a6b7c8d9e0f1
Revises: f6a7b8c9d0e1
Create Date: 2026-07-29 14:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a6b7c8d9e0f1"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("is_incognito", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        op.f("ix_chats_is_incognito"),
        "chats",
        ["is_incognito"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chats_is_incognito"), table_name="chats")
    op.drop_column("chats", "is_incognito")
