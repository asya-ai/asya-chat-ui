"""instance provider configs

Revision ID: j3c4d5e6f7a8
Revises: i2b3c4d5e6f7
Create Date: 2026-08-31 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "i2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_provider_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("provider_type", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=True),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column("config_json", sa.String(), nullable=True),
        sa.Column("migrated_from_env", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", name="uq_instance_provider_configs_provider"),
    )
    op.create_index(
        op.f("ix_instance_provider_configs_provider"),
        "instance_provider_configs",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_instance_provider_configs_provider_type"),
        "instance_provider_configs",
        ["provider_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_instance_provider_configs_provider_type"),
        table_name="instance_provider_configs",
    )
    op.drop_index(
        op.f("ix_instance_provider_configs_provider"),
        table_name="instance_provider_configs",
    )
    op.drop_table("instance_provider_configs")
