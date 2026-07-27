"""organization retention policies and durable usage identities

Revision ID: f6a7b8c9d0e1
Revises: d2e3f4a5b6c7
Create Date: 2026-07-27 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("file_retention_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "orgs",
        sa.Column("chat_retention_days", sa.Integer(), nullable=True),
    )

    op.add_column(
        "chats",
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        """
        UPDATE chats
        SET last_activity_at = COALESCE(
            (SELECT MAX(chat_messages.created_at)
             FROM chat_messages
             WHERE chat_messages.chat_id = chats.id),
            created_at
        )
        """
    )
    op.alter_column("chats", "last_activity_at", nullable=False)
    op.create_index("ix_chats_last_activity_at", "chats", ["last_activity_at"])

    op.add_column(
        "usage_events",
        sa.Column("org_name_snapshot", sa.String(), nullable=True),
    )
    op.add_column(
        "usage_events",
        sa.Column("user_name_snapshot", sa.String(), nullable=True),
    )
    op.execute(
        """
        UPDATE usage_events AS usage
        SET org_name_snapshot = orgs.name,
            user_name_snapshot = COALESCE(users.display_name, users.username, users.email)
        FROM orgs, users
        WHERE usage.org_id = orgs.id AND usage.user_id = users.id
        """
    )
    op.alter_column("usage_events", "org_name_snapshot", nullable=False)
    op.alter_column("usage_events", "user_name_snapshot", nullable=False)

    for column, target in (
        ("org_id", "orgs"),
        ("user_id", "users"),
        ("chat_id", "chats"),
        ("message_id", "chat_messages"),
    ):
        op.drop_constraint(
            f"usage_events_{column}_fkey", "usage_events", type_="foreignkey"
        )
        if column in {"org_id", "user_id"}:
            op.alter_column("usage_events", column, nullable=True)
        op.create_foreign_key(
            f"usage_events_{column}_fkey",
            "usage_events",
            target,
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for column, target in (
        ("message_id", "chat_messages"),
        ("chat_id", "chats"),
        ("user_id", "users"),
        ("org_id", "orgs"),
    ):
        op.drop_constraint(
            f"usage_events_{column}_fkey", "usage_events", type_="foreignkey"
        )
        op.create_foreign_key(
            f"usage_events_{column}_fkey",
            "usage_events",
            target,
            [column],
            ["id"],
        )
        if column in {"org_id", "user_id"}:
            op.alter_column("usage_events", column, nullable=False)

    op.drop_column("usage_events", "user_name_snapshot")
    op.drop_column("usage_events", "org_name_snapshot")
    op.drop_index("ix_chats_last_activity_at", table_name="chats")
    op.drop_column("chats", "last_activity_at")
    op.drop_column("orgs", "chat_retention_days")
    op.drop_column("orgs", "file_retention_days")
