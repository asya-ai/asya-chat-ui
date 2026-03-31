"""chat share token

Revision ID: 9f2d6c1a7b3e
Revises: 7b4d2c1e9a8f
Create Date: 2026-03-31 12:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f2d6c1a7b3e"
down_revision: Union[str, Sequence[str], None] = "7b4d2c1e9a8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("share_token", sa.String(), nullable=True))
    op.create_index("ix_chats_share_token", "chats", ["share_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_chats_share_token", table_name="chats")
    op.drop_column("chats", "share_token")
