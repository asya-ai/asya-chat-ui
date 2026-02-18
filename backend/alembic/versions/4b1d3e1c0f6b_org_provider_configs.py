"""org provider configs

Revision ID: 4b1d3e1c0f6b
Revises: 0836e1c65b03
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4b1d3e1c0f6b"
down_revision = "0836e1c65b03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_provider_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("api_key_override", sa.String(), nullable=True),
        sa.Column("base_url_override", sa.String(), nullable=True),
        sa.Column("endpoint_override", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_provider_configs_org_id"),
        "org_provider_configs",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_org_provider_configs_provider"),
        "org_provider_configs",
        ["provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_org_provider_configs_provider"), table_name="org_provider_configs")
    op.drop_index(op.f("ix_org_provider_configs_org_id"), table_name="org_provider_configs")
    op.drop_table("org_provider_configs")
