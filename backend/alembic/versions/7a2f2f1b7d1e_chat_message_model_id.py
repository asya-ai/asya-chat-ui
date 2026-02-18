"""chat message model id

Revision ID: 7a2f2f1b7d1e
Revises: 6b9f2f3a1c7d
Create Date: 2026-02-16 19:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a2f2f1b7d1e"
down_revision = "6b9f2f3a1c7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("model_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_chat_messages_model_id"),
        "chat_messages",
        ["model_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_chat_messages_model_id",
        "chat_messages",
        "chat_models",
        ["model_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_chat_messages_model_id", "chat_messages", type_="foreignkey")
    op.drop_index(op.f("ix_chat_messages_model_id"), table_name="chat_messages")
    op.drop_column("chat_messages", "model_id")
