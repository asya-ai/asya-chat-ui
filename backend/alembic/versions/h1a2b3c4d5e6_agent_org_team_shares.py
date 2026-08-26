"""agent org and team shares

Revision ID: h1a2b3c4d5e6
Revises: g7a8b9c0d1e2
Create Date: 2026-08-24 15:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "g7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("org_access_role", sa.String(length=32), nullable=True),
    )
    op.create_table(
        "agent_team_access",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "team_id", name="uq_agent_team_access_agent_team"),
    )
    op.create_index("ix_agent_team_access_agent_id", "agent_team_access", ["agent_id"])
    op.create_index("ix_agent_team_access_team_id", "agent_team_access", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_team_access_team_id", table_name="agent_team_access")
    op.drop_index("ix_agent_team_access_agent_id", table_name="agent_team_access")
    op.drop_table("agent_team_access")
    op.drop_column("agents", "org_access_role")
