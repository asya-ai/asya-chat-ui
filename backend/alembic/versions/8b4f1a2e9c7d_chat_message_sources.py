"""chat message sources

Revision ID: 8b4f1a2e9c7d
Revises: 6d7c9a2b1f45
Create Date: 2026-02-17 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8b4f1a2e9c7d"
down_revision = "6d7c9a2b1f45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("sources", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "sources")
