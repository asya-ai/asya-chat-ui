"""drop exec_enabled column

Revision ID: f1c2d3e4f5a6
Revises: e2f3a4b5c6d7
Create Date: 2026-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "f1c2d3e4f5a6"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("orgs", "exec_enabled")


def downgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("exec_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("orgs", "exec_enabled", server_default=None)
