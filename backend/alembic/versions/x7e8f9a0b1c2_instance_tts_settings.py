"""instance_tts_settings

Revision ID: x7e8f9a0b1c2
Revises: w6d7e8f9a0b1
Create Date: 2026-10-26 17:40:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "w6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_tts_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=True),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("voice_id", sa.String(), nullable=True),
        sa.Column("voice_label", sa.String(), nullable=True),
        sa.Column("config_json", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_instance_tts_settings_provider"),
        "instance_tts_settings",
        ["provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_instance_tts_settings_provider"),
        table_name="instance_tts_settings",
    )
    op.drop_table("instance_tts_settings")
