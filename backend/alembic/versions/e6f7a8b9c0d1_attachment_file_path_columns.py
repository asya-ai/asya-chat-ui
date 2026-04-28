"""add file_path columns for attachment storage

Revision ID: e6f7a8b9c0d1
Revises: b3c4d5e6f7a8
Create Date: 2026-04-28 12:12:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_message_attachments",
        sa.Column("file_path", sa.String(), nullable=True),
    )
    op.alter_column(
        "chat_message_attachments",
        "data_base64",
        existing_type=sa.String(),
        nullable=True,
    )

    op.add_column(
        "chat_uploads",
        sa.Column("file_path", sa.String(), nullable=True),
    )
    op.alter_column(
        "chat_uploads",
        "data_base64",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "chat_uploads",
        "data_base64",
        existing_type=sa.String(),
        nullable=False,
    )
    op.drop_column("chat_uploads", "file_path")

    op.alter_column(
        "chat_message_attachments",
        "data_base64",
        existing_type=sa.String(),
        nullable=False,
    )
    op.drop_column("chat_message_attachments", "file_path")
