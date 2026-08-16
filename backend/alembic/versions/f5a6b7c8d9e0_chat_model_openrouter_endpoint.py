"""add chat model openrouter endpoint

Revision ID: f5a6b7c8d9e0
Revises: e8f9a0b1c2d3
Create Date: 2026-08-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_models", sa.Column("openrouter_endpoint", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chat_models", "openrouter_endpoint")
