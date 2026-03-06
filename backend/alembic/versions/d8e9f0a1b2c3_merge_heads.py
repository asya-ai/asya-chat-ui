"""merge heads

Revision ID: d8e9f0a1b2c3
Revises: b1c2d3e4f5a7, c7d8e9f0a1b2
Create Date: 2026-03-02 15:05:00.000000

"""

from alembic import op  # noqa: F401


# revision identifiers, used by Alembic.
revision = "d8e9f0a1b2c3"
down_revision = ("b1c2d3e4f5a7", "c7d8e9f0a1b2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
