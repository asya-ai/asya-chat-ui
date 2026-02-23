"""chat model order

Revision ID: f3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-02-19 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "f3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_models",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (
                ORDER BY display_name, model_name, id
            ) AS rn
            FROM chat_models
        )
        UPDATE chat_models
        SET display_order = ordered.rn
        FROM ordered
        WHERE chat_models.id = ordered.id
        """
    )
    op.alter_column("chat_models", "display_order", server_default=None)


def downgrade() -> None:
    op.drop_column("chat_models", "display_order")
