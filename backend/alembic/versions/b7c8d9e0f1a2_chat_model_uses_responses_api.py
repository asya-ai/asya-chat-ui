"""chat model uses_responses_api

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-08-03 12:50:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_models",
        sa.Column("uses_responses_api", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_models", "uses_responses_api")
