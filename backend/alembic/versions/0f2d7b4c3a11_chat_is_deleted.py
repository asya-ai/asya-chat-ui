"""chat is deleted

Revision ID: 0f2d7b4c3a11
Revises: 9c1f2a7b0d3e
Create Date: 2026-02-16 20:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0f2d7b4c3a11"
down_revision = "9c1f2a7b0d3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        op.f("ix_chats_is_deleted"),
        "chats",
        ["is_deleted"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chats_is_deleted"), table_name="chats")
    op.drop_column("chats", "is_deleted")
