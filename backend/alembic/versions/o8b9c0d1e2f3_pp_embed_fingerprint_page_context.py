"""pitchpatterns embed fingerprint and page_context

Revision ID: o8b9c0d1e2f3
Revises: n7a8b9c0d1e2
Create Date: 2026-09-22 03:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "n7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_user_installs",
        sa.Column("credential_fingerprint", sa.String(), nullable=True),
    )
    op.create_index(
        op.f("ix_integration_user_installs_credential_fingerprint"),
        "integration_user_installs",
        ["credential_fingerprint"],
        unique=False,
    )
    op.add_column(
        "chat_messages",
        sa.Column("page_context", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "page_context")
    op.drop_index(
        op.f("ix_integration_user_installs_credential_fingerprint"),
        table_name="integration_user_installs",
    )
    op.drop_column("integration_user_installs", "credential_fingerprint")
