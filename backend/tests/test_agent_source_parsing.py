from __future__ import annotations

import pytest

from app.services.agents.source_parsing import extract_text_from_file


def test_rejects_image_files_instead_of_decoding_binary_content() -> None:
    with pytest.raises(ValueError, match="cannot be indexed as text"):
        extract_text_from_file(
            file_name="diagram.png",
            content_type="image/png",
            data=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        )


def test_removes_nul_bytes_from_extracted_text() -> None:
    assert (
        extract_text_from_file(
            file_name="notes.txt",
            content_type="text/plain",
            data=b"First\x00 line\r\nSecond line",
        )
        == "First line\nSecond line"
    )
