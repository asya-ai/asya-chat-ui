"""prompt user shares

Revision ID: e8f9a0b1c2d3
Revises: e7f8a9b0c1d2
Create Date: 2026-08-12 21:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_user_shares",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prompt_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_id", "user_id", name="uq_prompt_user_shares_prompt_user"),
    )
    op.create_index("ix_prompt_user_shares_prompt_id", "prompt_user_shares", ["prompt_id"])
    op.create_index("ix_prompt_user_shares_user_id", "prompt_user_shares", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_prompt_user_shares_user_id", table_name="prompt_user_shares")
    op.drop_index("ix_prompt_user_shares_prompt_id", table_name="prompt_user_shares")
    op.drop_table("prompt_user_shares")
