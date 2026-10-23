"""integration write preferences + exec_policy prompt cleanup

Revision ID: v5c6d7e8f9a0
Revises: u4b5c6d7e8f9
Create Date: 2026-10-21 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "u4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_write_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("always_allow", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "org_id",
            "provider_id",
            name="uq_integration_write_preferences_user_org_provider",
        ),
    )
    op.create_index(
        op.f("ix_integration_write_preferences_user_id"),
        "integration_write_preferences",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_write_preferences_org_id"),
        "integration_write_preferences",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_write_preferences_provider_id"),
        "integration_write_preferences",
        ["provider_id"],
        unique=False,
    )
    op.execute("UPDATE orgs SET exec_policy = 'always' WHERE exec_policy = 'prompt'")


def downgrade() -> None:
    op.drop_index(
        op.f("ix_integration_write_preferences_provider_id"),
        table_name="integration_write_preferences",
    )
    op.drop_index(
        op.f("ix_integration_write_preferences_org_id"),
        table_name="integration_write_preferences",
    )
    op.drop_index(
        op.f("ix_integration_write_preferences_user_id"),
        table_name="integration_write_preferences",
    )
    op.drop_table("integration_write_preferences")
