"""add org oidc settings and user auth provider

Revision ID: 9a1b2c3d4e5f
Revises: 526eeeb8086a
Create Date: 2026-02-25
"""

from __future__ import annotations

import re
from typing import Iterable

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9a1b2c3d4e5f"
down_revision = "526eeeb8086a"
branch_labels = None
depends_on = None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "org"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            sa.String(),
            nullable=False,
            server_default="local",
        ),
    )
    op.add_column(
        "orgs",
        sa.Column("slug", sa.String(), nullable=True),
    )
    op.add_column(
        "orgs",
        sa.Column(
            "oidc_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("orgs", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("orgs", sa.Column("oidc_client_id", sa.String(), nullable=True))
    op.add_column("orgs", sa.Column("oidc_client_secret", sa.String(), nullable=True))
    op.add_column(
        "orgs",
        sa.Column(
            "oidc_scopes",
            sa.String(),
            nullable=False,
            server_default="openid email profile",
        ),
    )
    op.add_column(
        "orgs",
        sa.Column(
            "oidc_email_claim",
            sa.String(),
            nullable=False,
            server_default="email",
        ),
    )
    op.add_column(
        "orgs",
        sa.Column(
            "oidc_username_claim",
            sa.String(),
            nullable=True,
            server_default="preferred_username",
        ),
    )
    op.add_column("orgs", sa.Column("oidc_groups_claim", sa.String(), nullable=True))
    op.add_column(
        "orgs",
        sa.Column(
            "oidc_auto_create_users",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    connection = op.get_bind()
    orgs = connection.execute(sa.text("SELECT id, name FROM orgs")).fetchall()
    used: set[str] = set()
    for org_id, name in orgs:
        base = _slugify(name or "")
        slug = base
        if slug in used:
            slug = f"{base}-{str(org_id)[:6]}"
        used.add(slug)
        connection.execute(
            sa.text("UPDATE orgs SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": org_id},
        )

    op.alter_column("orgs", "slug", nullable=False)
    op.create_index("ix_orgs_slug", "orgs", ["slug"], unique=True)

    op.alter_column("users", "auth_provider", server_default=None)
    op.alter_column("orgs", "oidc_enabled", server_default=None)
    op.alter_column("orgs", "oidc_scopes", server_default=None)
    op.alter_column("orgs", "oidc_email_claim", server_default=None)
    op.alter_column("orgs", "oidc_username_claim", server_default=None)
    op.alter_column("orgs", "oidc_auto_create_users", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_orgs_slug", table_name="orgs")
    op.drop_column("orgs", "oidc_auto_create_users")
    op.drop_column("orgs", "oidc_groups_claim")
    op.drop_column("orgs", "oidc_username_claim")
    op.drop_column("orgs", "oidc_email_claim")
    op.drop_column("orgs", "oidc_scopes")
    op.drop_column("orgs", "oidc_client_secret")
    op.drop_column("orgs", "oidc_client_id")
    op.drop_column("orgs", "oidc_issuer")
    op.drop_column("orgs", "oidc_enabled")
    op.drop_column("orgs", "slug")
    op.drop_column("users", "auth_provider")
