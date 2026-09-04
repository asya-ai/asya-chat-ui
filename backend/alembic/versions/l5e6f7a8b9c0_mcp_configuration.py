"""mcp configuration tables

Revision ID: l5e6f7a8b9c0
Revises: k4d5e6f7a8b9
Create Date: 2026-09-02 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "k4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("allow_org_servers", sa.Boolean(), nullable=False),
        sa.Column("allow_user_servers", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO mcp_settings (id, allow_org_servers, allow_user_servers, updated_at) "
            "VALUES (1, false, false, NOW())"
        )
    )

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("transport", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("command", sa.String(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("stdio_env", sa.JSON(), nullable=True),
        sa.Column("include_tools", sa.Boolean(), nullable=False),
        sa.Column("include_resources", sa.Boolean(), nullable=False),
        sa.Column("include_prompts", sa.Boolean(), nullable=False),
        sa.Column("tool_allowlist", sa.JSON(), nullable=True),
        sa.Column("tool_blocklist", sa.JSON(), nullable=True),
        sa.Column("auth_type", sa.String(), nullable=False),
        sa.Column("auth_config", sa.String(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_mcp_servers_slug"),
    )
    op.create_index(op.f("ix_mcp_servers_slug"), "mcp_servers", ["slug"], unique=False)
    op.create_index(op.f("ix_mcp_servers_scope"), "mcp_servers", ["scope"], unique=False)
    op.create_index(op.f("ix_mcp_servers_org_id"), "mcp_servers", ["org_id"], unique=False)
    op.create_index(op.f("ix_mcp_servers_user_id"), "mcp_servers", ["user_id"], unique=False)

    op.create_table(
        "mcp_org_settings",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("allow_user_servers", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("org_id"),
    )

    op.create_table(
        "mcp_org_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("instance_server_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("auth_type", sa.String(), nullable=True),
        sa.Column("auth_config", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["instance_server_id"], ["mcp_servers.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "instance_server_id", name="uq_mcp_org_bindings_org_server"
        ),
    )
    op.create_index(
        op.f("ix_mcp_org_bindings_org_id"), "mcp_org_bindings", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_mcp_org_bindings_instance_server_id"),
        "mcp_org_bindings",
        ["instance_server_id"],
        unique=False,
    )

    op.create_table(
        "mcp_user_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("auth_config", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "server_id", name="uq_mcp_user_connections_user_server"
        ),
    )
    op.create_index(
        op.f("ix_mcp_user_connections_user_id"),
        "mcp_user_connections",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_user_connections_server_id"),
        "mcp_user_connections",
        ["server_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_user_connections_status"),
        "mcp_user_connections",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_user_connections_status"), table_name="mcp_user_connections")
    op.drop_index(op.f("ix_mcp_user_connections_server_id"), table_name="mcp_user_connections")
    op.drop_index(op.f("ix_mcp_user_connections_user_id"), table_name="mcp_user_connections")
    op.drop_table("mcp_user_connections")
    op.drop_index(
        op.f("ix_mcp_org_bindings_instance_server_id"), table_name="mcp_org_bindings"
    )
    op.drop_index(op.f("ix_mcp_org_bindings_org_id"), table_name="mcp_org_bindings")
    op.drop_table("mcp_org_bindings")
    op.drop_table("mcp_org_settings")
    op.drop_index(op.f("ix_mcp_servers_user_id"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_org_id"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_scope"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_slug"), table_name="mcp_servers")
    op.drop_table("mcp_servers")
    op.drop_table("mcp_settings")
