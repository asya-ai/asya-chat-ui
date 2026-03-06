"""add chat generation tasks

Revision ID: c7d8e9f0a1b2
Revises: a7b8c9d0e1f2
Create Date: 2026-03-01 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    generation_status = postgresql.ENUM(
        "queued",
        "running",
        "streaming",
        "completed",
        "failed",
        "cancelled",
        name="generationstatus",
        create_type=False,
    )
    generation_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "chat_generation_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", generation_status, nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_messages.id"]),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.ForeignKeyConstraint(["user_message_id"], ["chat_messages.id"]),
    )
    op.create_index(
        "ix_chat_generation_tasks_chat_id", "chat_generation_tasks", ["chat_id"]
    )
    op.create_index(
        "ix_chat_generation_tasks_user_message_id",
        "chat_generation_tasks",
        ["user_message_id"],
    )
    op.create_index(
        "ix_chat_generation_tasks_assistant_message_id",
        "chat_generation_tasks",
        ["assistant_message_id"],
    )
    op.create_index(
        "ix_chat_generation_tasks_status", "chat_generation_tasks", ["status"]
    )

    op.create_table(
        "chat_generation_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["chat_generation_tasks.id"]),
    )
    op.create_index(
        "ix_chat_generation_events_task_id", "chat_generation_events", ["task_id"]
    )
    op.create_index(
        "ix_chat_generation_events_event_type",
        "chat_generation_events",
        ["event_type"],
    )
    op.create_index(
        "ix_chat_generation_events_sequence",
        "chat_generation_events",
        ["sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_generation_events_sequence", table_name="chat_generation_events")
    op.drop_index("ix_chat_generation_events_event_type", table_name="chat_generation_events")
    op.drop_index("ix_chat_generation_events_task_id", table_name="chat_generation_events")
    op.drop_table("chat_generation_events")

    op.drop_index(
        "ix_chat_generation_tasks_status", table_name="chat_generation_tasks"
    )
    op.drop_index(
        "ix_chat_generation_tasks_assistant_message_id",
        table_name="chat_generation_tasks",
    )
    op.drop_index(
        "ix_chat_generation_tasks_user_message_id",
        table_name="chat_generation_tasks",
    )
    op.drop_index(
        "ix_chat_generation_tasks_chat_id", table_name="chat_generation_tasks"
    )
    op.drop_table("chat_generation_tasks")

    generation_status = postgresql.ENUM(
        "queued",
        "running",
        "streaming",
        "completed",
        "failed",
        "cancelled",
        name="generationstatus",
        create_type=False,
    )
    generation_status.drop(op.get_bind(), checkfirst=True)
