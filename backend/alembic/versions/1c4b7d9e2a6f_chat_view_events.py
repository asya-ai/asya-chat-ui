"""chat view events

Revision ID: 1c4b7d9e2a6f
Revises: 9f2d6c1a7b3e
Create Date: 2026-03-31 13:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1c4b7d9e2a6f"
down_revision: Union[str, Sequence[str], None] = "9f2d6c1a7b3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_view_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("viewer_user_id", sa.Uuid(), nullable=True),
        sa.Column("viewer_label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.ForeignKeyConstraint(["viewer_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_view_events_chat_id", "chat_view_events", ["chat_id"], unique=False)
    op.create_index(
        "ix_chat_view_events_viewer_user_id",
        "chat_view_events",
        ["viewer_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_view_events_viewer_user_id", table_name="chat_view_events")
    op.drop_index("ix_chat_view_events_chat_id", table_name="chat_view_events")
    op.drop_table("chat_view_events")
