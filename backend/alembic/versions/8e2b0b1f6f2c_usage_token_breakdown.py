"""usage token breakdown

Revision ID: 8e2b0b1f6f2c
Revises: 7a2f2f1b7d1e
Create Date: 2026-02-16 19:25:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8e2b0b1f6f2c"
down_revision = "7a2f2f1b7d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage_events",
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage_events",
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage_events",
        sa.Column("thinking_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("usage_events", "thinking_tokens")
    op.drop_column("usage_events", "cached_tokens")
    op.drop_column("usage_events", "output_tokens")
    op.drop_column("usage_events", "input_tokens")
