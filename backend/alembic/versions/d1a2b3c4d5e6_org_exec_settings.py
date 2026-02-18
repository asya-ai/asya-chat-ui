"""org exec settings

Revision ID: d1a2b3c4d5e6
Revises: c4e8a1b2c3d4
Create Date: 2026-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "d1a2b3c4d5e6"
down_revision = "c4e8a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("exec_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "orgs",
        sa.Column(
            "exec_network_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.alter_column("orgs", "exec_enabled", server_default=None)
    op.alter_column("orgs", "exec_network_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("orgs", "exec_network_enabled")
    op.drop_column("orgs", "exec_enabled")
