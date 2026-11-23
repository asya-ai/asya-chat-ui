"""generation events (task_id, sequence) index

Every generation does `max(sequence) WHERE task_id = ?` before it starts.
With only single-column indexes Postgres walks ix_chat_generation_events_sequence
backwards over millions of rows (~2.3s on prod). A composite index makes it
an index-only lookup and also serves `task_id = ? AND sequence > ? ORDER BY sequence`.

Revision ID: y8f9a0b1c2d3
Revises: x7e8f9a0b1c2
Create Date: 2026-11-20 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "y8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "x7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_chat_generation_events_task_id_sequence"


def upgrade() -> None:
    # CONCURRENTLY: the table is large and written on every streamed token;
    # a plain CREATE INDEX would block chat generation while it builds.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
            "ON chat_generation_events (task_id, sequence)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
