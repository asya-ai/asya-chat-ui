"""rename prompt visibility space to project

Revision ID: g7a8b9c0d1e2
Revises: d8e9f0a1b2c3, f3b4c5d6e7f8, b7c8d9e0f1a2, c1d2e3f4a5b6, c8d9e0f1a2b3
Create Date: 2026-08-20 15:40:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "g7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = (
    "d8e9f0a1b2c3",
    "f3b4c5d6e7f8",
    "b7c8d9e0f1a2",
    "c1d2e3f4a5b6",
    "c8d9e0f1a2b3",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE prompts SET visibility = 'project' WHERE visibility = 'space'")


def downgrade() -> None:
    op.execute("UPDATE prompts SET visibility = 'space' WHERE visibility = 'project'")
