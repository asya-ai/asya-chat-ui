"""add chat model reasoning effort

Revision ID: b2c4d6e7f8a9
Revises: 8b4f1a2e9c7d
Create Date: 2026-02-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c4d6e7f8a9"
down_revision = "8b4f1a2e9c7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_models", sa.Column("reasoning_effort", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chat_models", "reasoning_effort")
