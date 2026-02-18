"""chat message branches

Revision ID: 6b9f2f3a1c7d
Revises: 5c91a19f6f2b
Create Date: 2026-02-16 18:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6b9f2f3a1c7d"
down_revision = "5c91a19f6f2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("parent_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("branch_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        op.f("ix_chat_messages_parent_id"),
        "chat_messages",
        ["parent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_messages_branch_id"),
        "chat_messages",
        ["branch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_messages_is_current"),
        "chat_messages",
        ["is_current"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_chat_messages_parent_id",
        "chat_messages",
        "chat_messages",
        ["parent_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_chat_messages_parent_id", "chat_messages", type_="foreignkey")
    op.drop_index(op.f("ix_chat_messages_is_current"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_branch_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_parent_id"), table_name="chat_messages")
    op.drop_column("chat_messages", "is_current")
    op.drop_column("chat_messages", "branch_id")
    op.drop_column("chat_messages", "parent_id")
