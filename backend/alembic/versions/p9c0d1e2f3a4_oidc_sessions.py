"""oidc sessions for SSO refresh tokens

Revision ID: p9c0d1e2f3a4
Revises: o8b9c0d1e2f3
Create Date: 2026-09-30 16:25:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "o8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oidc_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_oidc_sessions_user_id"), "oidc_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_oidc_sessions_org_id"), "oidc_sessions", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_oidc_sessions_org_id"), table_name="oidc_sessions")
    op.drop_index(op.f("ix_oidc_sessions_user_id"), table_name="oidc_sessions")
    op.drop_table("oidc_sessions")
