"""migrate attachment blobs from db to disk

Revision ID: i2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-08-26 10:00:00.000000
"""

from __future__ import annotations

import base64
import logging
from typing import Sequence, Union
from uuid import UUID

from alembic import op
from sqlalchemy import text

from app.services.file_storage import (
    write_chat_attachment_file,
    write_chat_upload_file,
)

logger = logging.getLogger("alembic.attachment_migrate")

# revision identifiers, used by Alembic.
revision: str = "i2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "h1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    uploads = conn.execute(
        text(
            """
            SELECT id, chat_id, file_name, data_base64
            FROM chat_uploads
            WHERE data_base64 IS NOT NULL
              AND data_base64 <> ''
              AND (file_path IS NULL OR file_path = '')
            """
        )
    ).mappings().all()
    for row in uploads:
        try:
            upload_id = UUID(str(row["id"]))
            chat_id = UUID(str(row["chat_id"]))
            relative_path, _size = write_chat_upload_file(
                chat_id=chat_id,
                upload_id=upload_id,
                file_name=str(row["file_name"] or "upload"),
                data_base64=str(row["data_base64"]),
            )
            conn.execute(
                text(
                    """
                    UPDATE chat_uploads
                    SET file_path = :file_path, data_base64 = NULL
                    WHERE id = :id
                    """
                ),
                {"file_path": relative_path, "id": row["id"]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed migrating chat_upload %s: %s", row["id"], exc)

    attachments = conn.execute(
        text(
            """
            SELECT a.id, a.message_id, a.file_name, a.data_base64, m.chat_id
            FROM chat_message_attachments a
            JOIN chat_messages m ON m.id = a.message_id
            WHERE a.data_base64 IS NOT NULL
              AND a.data_base64 <> ''
              AND (a.file_path IS NULL OR a.file_path = '')
            """
        )
    ).mappings().all()
    for row in attachments:
        try:
            # Validate base64 early so corrupt rows are skipped cleanly.
            base64.b64decode(str(row["data_base64"]), validate=False)
            attachment_id = UUID(str(row["id"]))
            message_id = UUID(str(row["message_id"]))
            chat_id = UUID(str(row["chat_id"]))
            relative_path, _size = write_chat_attachment_file(
                chat_id=chat_id,
                message_id=message_id,
                attachment_id=attachment_id,
                file_name=str(row["file_name"] or "attachment"),
                data_base64=str(row["data_base64"]),
            )
            conn.execute(
                text(
                    """
                    UPDATE chat_message_attachments
                    SET file_path = :file_path, data_base64 = NULL
                    WHERE id = :id
                    """
                ),
                {"file_path": relative_path, "id": row["id"]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed migrating chat_message_attachment %s: %s", row["id"], exc
            )


def downgrade() -> None:
    # Disk files are left in place; blobs are not re-hydrated into Postgres.
    pass
