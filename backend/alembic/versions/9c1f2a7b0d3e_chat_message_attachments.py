"""chat message attachments

Revision ID: 9c1f2a7b0d3e
Revises: 8e2b0b1f6f2c
Create Date: 2026-02-16 19:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9c1f2a7b0d3e"
down_revision = "8e2b0b1f6f2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_message_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("data_base64", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_message_attachments_message_id"),
        "chat_message_attachments",
        ["message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_chat_message_attachments_message_id"),
        table_name="chat_message_attachments",
    )
    op.drop_table("chat_message_attachments")
