"""chat model cost tier override

Revision ID: r1e2f3a4b5c6
Revises: q0d1e2f3a4b5
Create Date: 2026-10-08 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "q0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_models",
        sa.Column("cost_tier_override", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_models", "cost_tier_override")
