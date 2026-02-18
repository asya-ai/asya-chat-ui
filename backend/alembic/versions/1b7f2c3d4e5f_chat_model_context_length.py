"""chat model context length

Revision ID: 1b7f2c3d4e5f
Revises: 0f2d7b4c3a11
Create Date: 2026-02-16 20:25:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1b7f2c3d4e5f"
down_revision = "0f2d7b4c3a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_models",
        sa.Column("context_length", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_models", "context_length")
