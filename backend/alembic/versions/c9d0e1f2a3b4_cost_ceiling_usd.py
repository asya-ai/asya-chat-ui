"""cost ceiling usd on orgs and users

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-12 13:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("cost_ceiling_usd", sa.Float(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("cost_ceiling_usd", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "cost_ceiling_usd")
    op.drop_column("orgs", "cost_ceiling_usd")
