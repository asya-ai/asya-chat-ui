"""chat uploads

Revision ID: 4e7a9c2b1d3f
Revises: d8e9f0a1b2c3
Create Date: 2026-03-24 21:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "4e7a9c2b1d3f"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("data_base64", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_uploads_chat_id"), "chat_uploads", ["chat_id"], unique=False)
    op.create_index(op.f("ix_chat_uploads_user_id"), "chat_uploads", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_uploads_user_id"), table_name="chat_uploads")
    op.drop_index(op.f("ix_chat_uploads_chat_id"), table_name="chat_uploads")
    op.drop_table("chat_uploads")
