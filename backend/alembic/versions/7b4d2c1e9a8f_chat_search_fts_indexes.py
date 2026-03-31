"""chat search fts indexes

Revision ID: 7b4d2c1e9a8f
Revises: 4e7a9c2b1d3f
Create Date: 2026-03-31 10:45:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7b4d2c1e9a8f"
down_revision: Union[str, Sequence[str], None] = "4e7a9c2b1d3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chats_title_fts "
        "ON chats USING gin (to_tsvector('simple', coalesce(title, '')))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_content_fts "
        "ON chat_messages USING gin (to_tsvector('simple', coalesce(content, ''))) "
        "WHERE is_current IS TRUE"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_content_fts")
    op.execute("DROP INDEX IF EXISTS ix_chats_title_fts")
