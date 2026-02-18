"""org status fields

Revision ID: 5c91a19f6f2b
Revises: 4b1d3e1c0f6b
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5c91a19f6f2b"
down_revision = "4b1d3e1c0f6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("orgs", sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("orgs", "is_active", server_default=None)
    op.alter_column("orgs", "is_frozen", server_default=None)


def downgrade() -> None:
    op.drop_column("orgs", "is_frozen")
    op.drop_column("orgs", "is_active")
