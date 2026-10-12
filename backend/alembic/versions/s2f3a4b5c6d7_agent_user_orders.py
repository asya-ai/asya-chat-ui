"""agent user orders

Revision ID: s2f3a4b5c6d7
Revises: r1e2f3a4b5c6
Create Date: 2026-10-08 11:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "r1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_user_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "org_id", "agent_id", name="uq_agent_user_orders_user_org_agent"
        ),
    )
    op.create_index(
        op.f("ix_agent_user_orders_user_id"), "agent_user_orders", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_user_orders_org_id"), "agent_user_orders", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_user_orders_agent_id"), "agent_user_orders", ["agent_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_user_orders_display_order"),
        "agent_user_orders",
        ["display_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_user_orders_display_order"), table_name="agent_user_orders")
    op.drop_index(op.f("ix_agent_user_orders_agent_id"), table_name="agent_user_orders")
    op.drop_index(op.f("ix_agent_user_orders_org_id"), table_name="agent_user_orders")
    op.drop_index(op.f("ix_agent_user_orders_user_id"), table_name="agent_user_orders")
    op.drop_table("agent_user_orders")
