"""prompt library

Revision ID: e7f8a9b0c1d2
Revises: d0e1f2a3b4c5
Create Date: 2026-08-12 18:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompts_org_id", "prompts", ["org_id"])
    op.create_index("ix_prompts_owner_user_id", "prompts", ["owner_user_id"])
    op.create_index("ix_prompts_agent_id", "prompts", ["agent_id"])
    op.create_index("ix_prompts_name", "prompts", ["name"])

    op.create_table(
        "prompt_team_shares",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prompt_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_id", "team_id", name="uq_prompt_team_shares_prompt_team"),
    )
    op.create_index("ix_prompt_team_shares_prompt_id", "prompt_team_shares", ["prompt_id"])
    op.create_index("ix_prompt_team_shares_team_id", "prompt_team_shares", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_prompt_team_shares_team_id", table_name="prompt_team_shares")
    op.drop_index("ix_prompt_team_shares_prompt_id", table_name="prompt_team_shares")
    op.drop_table("prompt_team_shares")
    op.drop_index("ix_prompts_name", table_name="prompts")
    op.drop_index("ix_prompts_agent_id", table_name="prompts")
    op.drop_index("ix_prompts_owner_user_id", table_name="prompts")
    op.drop_index("ix_prompts_org_id", table_name="prompts")
    op.drop_table("prompts")
