"""org web tools settings

Revision ID: 6d7c9a2b1f45
Revises: 3f4a2c9d7b6e
Create Date: 2026-02-17 11:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6d7c9a2b1f45"
down_revision = "3f4a2c9d7b6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("web_tools_enabled", sa.Boolean(), server_default=sa.text("false")),
    )
    op.add_column(
        "orgs",
        sa.Column("web_search_enabled", sa.Boolean(), server_default=sa.text("true")),
    )
    op.add_column(
        "orgs",
        sa.Column("web_scrape_enabled", sa.Boolean(), server_default=sa.text("true")),
    )
    op.add_column(
        "orgs",
        sa.Column("web_grounding_openai", sa.Boolean(), server_default=sa.text("false")),
    )
    op.add_column(
        "orgs",
        sa.Column("web_grounding_gemini", sa.Boolean(), server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("orgs", "web_grounding_gemini")
    op.drop_column("orgs", "web_grounding_openai")
    op.drop_column("orgs", "web_scrape_enabled")
    op.drop_column("orgs", "web_search_enabled")
    op.drop_column("orgs", "web_tools_enabled")
