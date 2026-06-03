from __future__ import annotations

import base64
from io import BytesIO
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from app.services.tools import pdf_tool
from app.services.tools.pdf_tool import PdfToolContext


def _pdf_base64() -> str:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_extract_pdf_reads_pending_attachments(monkeypatch) -> None:
    monkeypatch.setattr(pdf_tool, "_chat_attachments", lambda *_: [])
    pending_attachments = [
        {
            "file_name": "sample.pdf",
            "content_type": "application/pdf",
            "data_base64": _pdf_base64(),
        }
    ]

    result = await pdf_tool.extract_pdf(
        PdfToolContext(
            session=object(),  # type: ignore[arg-type]
            chat_id=str(uuid4()),
            pending_attachments=pending_attachments,
        )
    )

    assert result.output.get("error") is None
    assert result.output["file_name"] == "sample.pdf"
    assert result.output["page_count"] == 1
    assert isinstance(pending_attachments[0].get("attachment_id"), str)


@pytest.mark.asyncio
async def test_extract_pdf_reuses_assigned_pending_attachment_id(monkeypatch) -> None:
    monkeypatch.setattr(pdf_tool, "_chat_attachments", lambda *_: [])
    pending_attachments = [
        {
            "file_name": "manual.pdf",
            "content_type": "application/pdf",
            "data_base64": _pdf_base64(),
        }
    ]
    context = PdfToolContext(
        session=object(),  # type: ignore[arg-type]
        chat_id=str(uuid4()),
        pending_attachments=pending_attachments,
    )

    first = await pdf_tool.extract_pdf(context)
    attachment_id = first.output["attachment_id"]

    second = await pdf_tool.extract_pdf(context, attachment_id=attachment_id, page=1)

    assert second.output.get("error") is None
    assert second.output["attachment_id"] == attachment_id
