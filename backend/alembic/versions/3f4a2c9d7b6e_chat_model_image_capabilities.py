"""chat model image capabilities

Revision ID: 3f4a2c9d7b6e
Revises: 2c8d5e1a7f6b
Create Date: 2026-02-16 21:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3f4a2c9d7b6e"
down_revision = "2c8d5e1a7f6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_models",
        sa.Column("supports_image_input", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "chat_models",
        sa.Column("supports_image_output", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_models", "supports_image_output")
    op.drop_column("chat_models", "supports_image_input")
