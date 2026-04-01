"""user token version

Revision ID: a1b2c3d4e5f6
Revises: 1c4b7d9e2a6f
Create Date: 2026-04-01 09:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "1c4b7d9e2a6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE users SET token_version = 0 WHERE token_version IS NULL")
    op.alter_column("users", "token_version", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "token_version")
