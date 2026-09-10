"""chat subagent parent_chat_id and is_subagent

Revision ID: m6f7a8b9c0d1
Revises: l5e6f7a8b9c0
Create Date: 2026-09-09 14:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "l5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("parent_chat_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "chats",
        sa.Column(
            "is_subagent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        op.f("ix_chats_parent_chat_id"),
        "chats",
        ["parent_chat_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chats_is_subagent"),
        "chats",
        ["is_subagent"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_chats_parent_chat_id_chats"),
        "chats",
        "chats",
        ["parent_chat_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_chats_parent_chat_id_chats"), "chats", type_="foreignkey")
    op.drop_index(op.f("ix_chats_is_subagent"), table_name="chats")
    op.drop_index(op.f("ix_chats_parent_chat_id"), table_name="chats")
    op.drop_column("chats", "is_subagent")
    op.drop_column("chats", "parent_chat_id")
