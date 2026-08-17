"""add chat cowork documents

Revision ID: c8d9e0f1a2b3
Revises: f5a6b7c8d9e0
Create Date: 2026-08-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "c8d9e0f1a2b3"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_cowork_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_assistant_version", sa.Integer(), nullable=False),
        sa.Column("content_at_assistant_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_cowork_documents_chat_id"),
        "chat_cowork_documents",
        ["chat_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_cowork_documents_is_active"),
        "chat_cowork_documents",
        ["is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_chat_cowork_documents_is_active"), table_name="chat_cowork_documents"
    )
    op.drop_index(
        op.f("ix_chat_cowork_documents_chat_id"), table_name="chat_cowork_documents"
    )
    op.drop_table("chat_cowork_documents")
