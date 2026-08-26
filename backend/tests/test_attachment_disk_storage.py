from __future__ import annotations

import base64
from pathlib import Path
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    Chat,
    ChatMessage,
    ChatMessageAttachment,
    ChatUpload,
    Org,
    User,
)
from app.services.chat_attachments import (
    ResolvedAttachmentPayload,
    persist_resolved_attachments,
    persist_tool_attachment_dicts,
)
from app.services.file_storage import (
    attachment_bytes,
    store_chat_upload_bytes,
)


def _session(tmp_path: Path, monkeypatch) -> Session:
    monkeypatch.setattr(
        "app.services.file_storage.settings.files_base_dir",
        str(tmp_path),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Org.__table__,
            Chat.__table__,
            ChatMessage.__table__,
            ChatMessageAttachment.__table__,
            ChatUpload.__table__,
        ],
    )
    return Session(engine)


def test_persist_resolved_attachments_writes_disk_not_db(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    org = Org(name="o")
    user = User(email="u@example.com", hashed_password="x")
    session.add(org)
    session.add(user)
    session.commit()
    session.refresh(org)
    session.refresh(user)
    chat = Chat(org_id=org.id, user_id=user.id, title="t")
    session.add(chat)
    session.commit()
    session.refresh(chat)
    message = ChatMessage(chat_id=chat.id, role="user", content="hi", status="done")
    session.add(message)
    session.commit()
    session.refresh(message)

    payload = b"hello-image"
    attachments = persist_resolved_attachments(
        session,
        chat_id=chat.id,
        message_id=message.id,
        items=[
            ResolvedAttachmentPayload(
                file_name="a.png",
                content_type="image/png",
                data=payload,
            )
        ],
    )
    assert len(attachments) == 1
    att = attachments[0]
    assert att.data_base64 is None
    assert att.file_path
    assert (tmp_path / att.file_path).read_bytes() == payload
    assert attachment_bytes(file_path=att.file_path, data_base64=att.data_base64) == payload


def test_persist_tool_attachment_dicts_and_upload_bytes(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    org = Org(name="o")
    user = User(email="u2@example.com", hashed_password="x")
    session.add(org)
    session.add(user)
    session.commit()
    session.refresh(org)
    session.refresh(user)
    chat = Chat(org_id=org.id, user_id=user.id, title="t")
    session.add(chat)
    session.commit()
    session.refresh(chat)
    message = ChatMessage(chat_id=chat.id, role="assistant", content="", status="done")
    session.add(message)
    session.commit()
    session.refresh(message)

    raw = b"tool-png"
    stored = persist_tool_attachment_dicts(
        session,
        chat_id=chat.id,
        message_id=message.id,
        items=[
            {
                "file_name": "out.png",
                "content_type": "image/png",
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        ],
    )
    assert len(stored) == 1
    assert stored[0].data_base64 is None
    assert attachment_bytes(file_path=stored[0].file_path) == raw

    upload = ChatUpload(
        chat_id=chat.id,
        user_id=user.id,
        file_name="up.bin",
        content_type="application/octet-stream",
        data_base64=None,
    )
    session.add(upload)
    session.commit()
    session.refresh(upload)
    upload.file_path = store_chat_upload_bytes(
        chat_id=chat.id,
        upload_id=upload.id,
        file_name=upload.file_name,
        data=b"upload-bytes",
    )
    session.add(upload)
    session.commit()
    session.refresh(upload)
    assert upload.data_base64 is None
    assert attachment_bytes(file_path=upload.file_path) == b"upload-bytes"
    assert session.exec(select(ChatUpload).where(ChatUpload.id == upload.id)).first()
