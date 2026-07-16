from __future__ import annotations

import base64
from uuid import UUID, uuid4

from app.models import ChatMessageAttachment
from app.services.tools.code_execution import _collect_outputs, _validate_imports, _write_inputs


def _attachment(*, attachment_id: str, file_name: str, payload: bytes) -> ChatMessageAttachment:
    return ChatMessageAttachment(
        id=UUID(attachment_id),
        message_id=uuid4(),
        file_name=file_name,
        content_type="application/octet-stream",
        data_base64=base64.b64encode(payload).decode("ascii"),
    )


def test_validate_imports_allows_presentation_dependencies() -> None:
    _validate_imports("import geopandas as gpd\nimport shapefile\nfrom pptx import Presentation")


def test_write_inputs_stages_raw_attachments_without_preprocessing(tmp_path) -> None:
    shp_id = "11111111-1111-1111-1111-111111111111"
    zip_id = "22222222-2222-2222-2222-222222222222"
    attachments = [
        _attachment(attachment_id=shp_id, file_name="roads.shp", payload=b"shp-bytes"),
        _attachment(attachment_id=zip_id, file_name="bundle.zip", payload=b"zip-bytes"),
    ]

    inputs = _write_inputs(attachments, tmp_path)

    assert inputs == [
        {
            "name": "roads.shp",
            "path": f"/inputs/{shp_id}_roads.shp",
            "content_type": "application/octet-stream",
        },
        {
            "name": "bundle.zip",
            "path": f"/inputs/{zip_id}_bundle.zip",
            "content_type": "application/octet-stream",
        },
    ]
    assert (tmp_path / f"{shp_id}_roads.shp").read_bytes() == b"shp-bytes"
    assert (tmp_path / f"{zip_id}_bundle.zip").read_bytes() == b"zip-bytes"


def test_collect_outputs_keeps_large_files_as_attachments(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.tools.code_execution.settings.exec_max_output_file_bytes",
        3,
    )
    monkeypatch.setattr(
        "app.services.tools.code_execution.settings.attachments_max_file_bytes",
        20,
    )
    monkeypatch.setattr(
        "app.services.tools.code_execution.settings.attachments_max_total_bytes",
        20,
    )
    (tmp_path / "image.png").write_bytes(b"larger-than-inline")

    attachments, output_items = _collect_outputs(tmp_path)

    assert attachments == [
        {
            "file_name": "image.png",
            "content_type": "image/png",
            "data_base64": base64.b64encode(b"larger-than-inline").decode("ascii"),
        }
    ]
    assert output_items == []
