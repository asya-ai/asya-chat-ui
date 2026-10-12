"""whitelabel settings

Revision ID: q0d1e2f3a4b5
Revises: p9c0d1e2f3a4
Create Date: 2026-10-02 11:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "p9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whitelabel_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_title", sa.String(), nullable=True),
        sa.Column("logo_path", sa.String(), nullable=True),
        sa.Column("logo_content_type", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("whitelabel_settings")
