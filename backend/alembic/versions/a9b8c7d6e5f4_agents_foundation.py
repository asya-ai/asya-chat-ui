"""add agents foundation tables

Revision ID: a9b8c7d6e5f4
Revises: e6f7a8b9c0d1
Create Date: 2026-05-06 11:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("preferred_model_id", sa.Uuid(), nullable=True),
        sa.Column("master_prompt", sa.String(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["preferred_model_id"], ["chat_models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_name"), "agents", ["name"], unique=False)
    op.create_index(op.f("ix_agents_org_id"), "agents", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_agents_owner_user_id"), "agents", ["owner_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_agents_visibility"), "agents", ["visibility"], unique=False
    )

    op.create_table(
        "agent_access",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "user_id", name="uq_agent_access_agent_user"),
    )
    op.create_index(
        op.f("ix_agent_access_agent_id"), "agent_access", ["agent_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_access_role"), "agent_access", ["role"], unique=False
    )
    op.create_index(
        op.f("ix_agent_access_user_id"), "agent_access", ["user_id"], unique=False
    )

    op.create_table(
        "agent_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("content_text", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_sources_agent_id"), "agent_sources", ["agent_id"], unique=False
    )
    op.create_index(op.f("ix_agent_sources_kind"), "agent_sources", ["kind"], unique=False)
    op.create_index(
        op.f("ix_agent_sources_status"), "agent_sources", ["status"], unique=False
    )

    op.create_table(
        "agent_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("token_count_estimate", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["agent_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_chunks_agent_id"), "agent_chunks", ["agent_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_chunks_chunk_index"), "agent_chunks", ["chunk_index"], unique=False
    )
    op.create_index(
        op.f("ix_agent_chunks_source_id"), "agent_chunks", ["source_id"], unique=False
    )

    op.create_table(
        "agent_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["agent_chunks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_embeddings_chunk_id"),
        "agent_embeddings",
        ["chunk_id"],
        unique=False,
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["chat_models.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_runs_agent_id"), "agent_runs", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"], unique=False)
    op.create_index(op.f("ix_agent_runs_user_id"), "agent_runs", ["user_id"], unique=False)

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_messages_role"), "agent_messages", ["role"], unique=False
    )
    op.create_index(
        op.f("ix_agent_messages_run_id"), "agent_messages", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_messages_run_id"), table_name="agent_messages")
    op.drop_index(op.f("ix_agent_messages_role"), table_name="agent_messages")
    op.drop_table("agent_messages")

    op.drop_index(op.f("ix_agent_runs_user_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_agent_id"), table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index(op.f("ix_agent_embeddings_chunk_id"), table_name="agent_embeddings")
    op.drop_table("agent_embeddings")

    op.drop_index(op.f("ix_agent_chunks_source_id"), table_name="agent_chunks")
    op.drop_index(op.f("ix_agent_chunks_chunk_index"), table_name="agent_chunks")
    op.drop_index(op.f("ix_agent_chunks_agent_id"), table_name="agent_chunks")
    op.drop_table("agent_chunks")

    op.drop_index(op.f("ix_agent_sources_status"), table_name="agent_sources")
    op.drop_index(op.f("ix_agent_sources_kind"), table_name="agent_sources")
    op.drop_index(op.f("ix_agent_sources_agent_id"), table_name="agent_sources")
    op.drop_table("agent_sources")

    op.drop_index(op.f("ix_agent_access_user_id"), table_name="agent_access")
    op.drop_index(op.f("ix_agent_access_role"), table_name="agent_access")
    op.drop_index(op.f("ix_agent_access_agent_id"), table_name="agent_access")
    op.drop_table("agent_access")

    op.drop_index(op.f("ix_agents_visibility"), table_name="agents")
    op.drop_index(op.f("ix_agents_owner_user_id"), table_name="agents")
    op.drop_index(op.f("ix_agents_org_id"), table_name="agents")
    op.drop_index(op.f("ix_agents_name"), table_name="agents")
    op.drop_table("agents")

    # No PostgreSQL enum types to drop; all statuses/roles are plain strings.
