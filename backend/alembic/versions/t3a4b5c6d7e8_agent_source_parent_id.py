"""agent source parent_id for archive children

Revision ID: t3a4b5c6d7e8
Revises: s2f3a4b5c6d7
Create Date: 2026-10-20 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "s2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_sources",
        sa.Column("parent_source_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_sources_parent_source_id"),
        "agent_sources",
        ["parent_source_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_agent_sources_parent_source_id",
        "agent_sources",
        "agent_sources",
        ["parent_source_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_sources_parent_source_id", "agent_sources", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_agent_sources_parent_source_id"), table_name="agent_sources"
    )
    op.drop_column("agent_sources", "parent_source_id")
