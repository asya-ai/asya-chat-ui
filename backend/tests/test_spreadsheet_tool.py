from __future__ import annotations

import base64
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook

from app.services.tools import spreadsheet_tool
from app.services.tools.spreadsheet_tool import SpreadsheetToolContext


def _xlsx_base64(*, cells: list[tuple[str, str]]) -> str:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Data"
    for address, value in cells:
        sheet[address] = value
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_read_spreadsheet_pending_attachment(monkeypatch) -> None:
    monkeypatch.setattr(spreadsheet_tool, "_chat_attachments", lambda *_: [])
    pending = [
        {
            "file_name": "demo.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "data_base64": _xlsx_base64(cells=[("A1", "Hello"), ("B2", "World")]),
        }
    ]
    result = await spreadsheet_tool.read_spreadsheet(
        SpreadsheetToolContext(
            session=object(),  # type: ignore[arg-type]
            chat_id=str(uuid4()),
            pending_attachments=pending,
        ),
        max_rows=5,
    )
    assert result.output.get("error") is None
    assert result.output["sheet"] == "Data"
    assert len(result.output["rows"]) >= 1


@pytest.mark.asyncio
async def test_search_and_replace_spreadsheet(monkeypatch) -> None:
    monkeypatch.setattr(spreadsheet_tool, "_chat_attachments", lambda *_: [])
    pending = [
        {
            "file_name": "edit.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "data_base64": _xlsx_base64(cells=[("A1", "BESS"), ("A2", "bess note")]),
        }
    ]
    context = SpreadsheetToolContext(
        session=object(),  # type: ignore[arg-type]
        chat_id=str(uuid4()),
        pending_attachments=pending,
    )
    search = await spreadsheet_tool.search_spreadsheet(context, query="BESS")
    assert search.output.get("error") is None
    assert search.output["match_count"] >= 1

    replaced = await spreadsheet_tool.replace_spreadsheet_text(
        context, find="BESS", replace="Battery"
    )
    assert replaced.output.get("error") is None
    assert replaced.output["cells_changed"] >= 1
    assert replaced.attachments
    assert replaced.attachments[0]["file_name"].endswith(".xlsx")
