"""org exec policy

Revision ID: e2f3a4b5c6d7
Revises: d1a2b3c4d5e6
Create Date: 2026-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "d1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("exec_policy", sa.String(), nullable=False, server_default="off"),
    )
    op.alter_column("orgs", "exec_policy", server_default=None)


def downgrade() -> None:
    op.drop_column("orgs", "exec_policy")
