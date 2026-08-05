"""org teams for model access

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-05 14:45:00.000000
"""

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("oidc_group", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "oidc_group", name="uq_teams_org_oidc_group"),
    )
    op.create_index(op.f("ix_teams_org_id"), "teams", ["org_id"], unique=False)
    op.create_index(op.f("ix_teams_is_default"), "teams", ["is_default"], unique=False)
    op.create_index(op.f("ix_teams_oidc_group"), "teams", ["oidc_group"], unique=False)

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
    )
    op.create_index(
        op.f("ix_team_memberships_team_id"), "team_memberships", ["team_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_memberships_user_id"), "team_memberships", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_memberships_source"), "team_memberships", ["source"], unique=False
    )

    op.create_table(
        "team_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["chat_models.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "model_id", name="uq_team_models_team_model"),
    )
    op.create_index(op.f("ix_team_models_team_id"), "team_models", ["team_id"], unique=False)
    op.create_index(op.f("ix_team_models_model_id"), "team_models", ["model_id"], unique=False)

    conn = op.get_bind()
    orgs = conn.execute(sa.text("SELECT id FROM orgs")).fetchall()
    for (org_id,) in orgs:
        team_id = uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO teams (id, org_id, name, is_default, oidc_group, created_at) "
                "VALUES (:id, :org_id, :name, :is_default, NULL, CURRENT_TIMESTAMP)"
            ),
            {"id": team_id, "org_id": org_id, "name": "Default", "is_default": True},
        )
        enabled = conn.execute(
            sa.text(
                "SELECT model_id FROM org_models "
                "WHERE org_id = :org_id AND is_enabled IS TRUE"
            ),
            {"org_id": org_id},
        ).fetchall()
        for (model_id,) in enabled:
            conn.execute(
                sa.text(
                    "INSERT INTO team_models (id, team_id, model_id, is_enabled, created_at) "
                    "VALUES (:id, :team_id, :model_id, :is_enabled, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": uuid4(),
                    "team_id": team_id,
                    "model_id": model_id,
                    "is_enabled": True,
                },
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_team_models_model_id"), table_name="team_models")
    op.drop_index(op.f("ix_team_models_team_id"), table_name="team_models")
    op.drop_table("team_models")
    op.drop_index(op.f("ix_team_memberships_source"), table_name="team_memberships")
    op.drop_index(op.f("ix_team_memberships_user_id"), table_name="team_memberships")
    op.drop_index(op.f("ix_team_memberships_team_id"), table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_index(op.f("ix_teams_oidc_group"), table_name="teams")
    op.drop_index(op.f("ix_teams_is_default"), table_name="teams")
    op.drop_index(op.f("ix_teams_org_id"), table_name="teams")
    op.drop_table("teams")
