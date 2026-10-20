"""chat project visibility and message author

Revision ID: u4b5c6d7e8f9
Revises: t3a4b5c6d7e8
Create Date: 2026-10-20 18:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "t3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column(
            "is_hidden_from_others",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        op.f("ix_chats_is_hidden_from_others"),
        "chats",
        ["is_hidden_from_others"],
        unique=False,
    )

    op.add_column(
        "chat_messages",
        sa.Column("user_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_chat_messages_user_id"),
        "chat_messages",
        ["user_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_chat_messages_user_id_users"),
        "chat_messages",
        "users",
        ["user_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE chat_messages AS m
        SET user_id = c.user_id
        FROM chats AS c
        WHERE m.chat_id = c.id
          AND m.role = 'user'
          AND m.user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_chat_messages_user_id_users"),
        "chat_messages",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_chat_messages_user_id"), table_name="chat_messages")
    op.drop_column("chat_messages", "user_id")
    op.drop_index(op.f("ix_chats_is_hidden_from_others"), table_name="chats")
    op.drop_column("chats", "is_hidden_from_others")
