"""repair missing chat_messages status lifecycle columns

Revision ID: f4e5d6c7b8a9
Revises: e1f2a3b4c5d6
Create Date: 2026-05-06 16:09:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4e5d6c7b8a9"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("chat_messages")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("chat_messages")}

    if "status" not in existing_columns:
        op.add_column(
            "chat_messages",
            sa.Column("status", sa.String(), nullable=False, server_default="done"),
        )
        op.alter_column("chat_messages", "status", server_default=None)

    if "started_at" not in existing_columns:
        op.add_column("chat_messages", sa.Column("started_at", sa.DateTime(), nullable=True))

    if "completed_at" not in existing_columns:
        op.add_column("chat_messages", sa.Column("completed_at", sa.DateTime(), nullable=True))

    if "error_message" not in existing_columns:
        op.add_column("chat_messages", sa.Column("error_message", sa.String(), nullable=True))

    index_name = op.f("ix_chat_messages_status")
    if "status" in {column["name"] for column in inspector.get_columns("chat_messages")} and index_name not in existing_indexes:
        op.create_index(index_name, "chat_messages", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("chat_messages")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("chat_messages")}

    index_name = op.f("ix_chat_messages_status")
    if index_name in existing_indexes:
        op.drop_index(index_name, table_name="chat_messages")

    if "error_message" in existing_columns:
        op.drop_column("chat_messages", "error_message")
    if "completed_at" in existing_columns:
        op.drop_column("chat_messages", "completed_at")
    if "started_at" in existing_columns:
        op.drop_column("chat_messages", "started_at")
    if "status" in existing_columns:
        op.drop_column("chat_messages", "status")
