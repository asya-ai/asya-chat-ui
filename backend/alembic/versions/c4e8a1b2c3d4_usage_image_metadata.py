"""add image usage metadata to usage_events

Revision ID: c4e8a1b2c3d4
Revises: b2c4d6e7f8a9
Create Date: 2026-02-18 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4e8a1b2c3d4"
down_revision = "b2c4d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_events", sa.Column("image_width", sa.Integer(), nullable=True))
    op.add_column("usage_events", sa.Column("image_height", sa.Integer(), nullable=True))
    op.add_column("usage_events", sa.Column("image_count", sa.Integer(), nullable=True))
    op.add_column("usage_events", sa.Column("image_format", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_events", "image_format")
    op.drop_column("usage_events", "image_count")
    op.drop_column("usage_events", "image_height")
    op.drop_column("usage_events", "image_width")
