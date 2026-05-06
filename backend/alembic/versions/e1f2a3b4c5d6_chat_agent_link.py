"""add agent link to chats

Revision ID: e1f2a3b4c5d6
Revises: a9b8c7d6e5f4
Create Date: 2026-05-06 15:38:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("agent_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_chats_agent_id"), "chats", ["agent_id"], unique=False)
    op.create_foreign_key(
        "fk_chats_agent_id_agents",
        "chats",
        "agents",
        ["agent_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_chats_agent_id_agents", "chats", type_="foreignkey")
    op.drop_index(op.f("ix_chats_agent_id"), table_name="chats")
    op.drop_column("chats", "agent_id")
