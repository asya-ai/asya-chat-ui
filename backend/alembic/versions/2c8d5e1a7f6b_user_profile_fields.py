"""user profile fields

Revision ID: 2c8d5e1a7f6b
Revises: 1b7f2c3d4e5f
Create Date: 2026-02-16 20:55:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2c8d5e1a7f6b"
down_revision = "1b7f2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_column("users", "display_name")
    op.drop_column("users", "username")
