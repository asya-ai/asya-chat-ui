from __future__ import annotations

from app.services.agents.source_media import (
    guess_content_type,
    is_archive_file,
    is_image_file,
)


def test_detects_images_by_mime_and_extension() -> None:
    assert is_image_file(file_name="photo.PNG", content_type=None)
    assert is_image_file(file_name="x.bin", content_type="image/jpeg")
    assert not is_image_file(file_name="notes.pdf", content_type="application/pdf")


def test_detects_archives_by_mime_and_extension() -> None:
    assert is_archive_file(file_name="bundle.zip", content_type=None)
    assert is_archive_file(file_name="bundle.tar.gz", content_type=None)
    assert is_archive_file(file_name="x.bin", content_type="application/zip")
    assert not is_archive_file(file_name="notes.pdf", content_type="application/pdf")


def test_guess_content_type() -> None:
    assert guess_content_type("slide.png") == "image/png"
    assert guess_content_type("data.zip") == "application/zip"
