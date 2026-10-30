"""feature_events for analytics

Revision ID: w6d7e8f9a0b1
Revises: v5c6d7e8f9a0
Create Date: 2026-10-26 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "v5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_feature_events_org_id"), "feature_events", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_feature_events_user_id"), "feature_events", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_feature_events_chat_id"), "feature_events", ["chat_id"], unique=False
    )
    op.create_index(
        op.f("ix_feature_events_feature"), "feature_events", ["feature"], unique=False
    )
    op.create_index(
        "ix_feature_events_org_created",
        "feature_events",
        ["org_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feature_events_feature_created",
        "feature_events",
        ["feature", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feature_events_feature_created", table_name="feature_events")
    op.drop_index("ix_feature_events_org_created", table_name="feature_events")
    op.drop_index(op.f("ix_feature_events_feature"), table_name="feature_events")
    op.drop_index(op.f("ix_feature_events_chat_id"), table_name="feature_events")
    op.drop_index(op.f("ix_feature_events_user_id"), table_name="feature_events")
    op.drop_index(op.f("ix_feature_events_org_id"), table_name="feature_events")
    op.drop_table("feature_events")
