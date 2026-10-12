"""integrations framework tables

Revision ID: n7a8b9c0d1e2
Revises: m6f7a8b9c0d1
Create Date: 2026-09-21 22:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "m6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("origin", sa.String(), nullable=False, server_default="custom"),
    )
    op.add_column("mcp_servers", sa.Column("catalog_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_mcp_servers_origin"), "mcp_servers", ["origin"], unique=False)
    op.create_index(
        op.f("ix_mcp_servers_catalog_id"), "mcp_servers", ["catalog_id"], unique=False
    )

    op.create_table(
        "integration_admin_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_kind",
            "target_id",
            "scope",
            "org_id",
            name="uq_integration_admin_configs_target_scope_org",
        ),
    )
    op.create_index(
        op.f("ix_integration_admin_configs_target_kind"),
        "integration_admin_configs",
        ["target_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_admin_configs_target_id"),
        "integration_admin_configs",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_admin_configs_scope"),
        "integration_admin_configs",
        ["scope"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_admin_configs_org_id"),
        "integration_admin_configs",
        ["org_id"],
        unique=False,
    )

    op.create_table(
        "integration_catalog_enablements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("catalog_id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_id",
            "scope",
            "org_id",
            name="uq_integration_catalog_enablements_scope_org",
        ),
    )
    op.create_index(
        op.f("ix_integration_catalog_enablements_catalog_id"),
        "integration_catalog_enablements",
        ["catalog_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_catalog_enablements_scope"),
        "integration_catalog_enablements",
        ["scope"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_catalog_enablements_org_id"),
        "integration_catalog_enablements",
        ["org_id"],
        unique=False,
    )

    op.create_table(
        "integration_user_installs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_id", sa.String(), nullable=False),
        sa.Column("credentials", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "org_id",
            "catalog_id",
            name="uq_integration_user_installs_user_org_catalog",
        ),
    )
    op.create_index(
        op.f("ix_integration_user_installs_user_id"),
        "integration_user_installs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_user_installs_org_id"),
        "integration_user_installs",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_user_installs_catalog_id"),
        "integration_user_installs",
        ["catalog_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_user_installs_status"),
        "integration_user_installs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("integration_user_installs")
    op.drop_table("integration_catalog_enablements")
    op.drop_table("integration_admin_configs")
    op.drop_index(op.f("ix_mcp_servers_catalog_id"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_origin"), table_name="mcp_servers")
    op.drop_column("mcp_servers", "catalog_id")
    op.drop_column("mcp_servers", "origin")
