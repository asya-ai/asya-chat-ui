from __future__ import annotations

import io

from PIL import Image

from app.services.image_normalize import normalize_image_attachment


def _png_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), color=(20, 40, 60))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _bmp_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), color=(200, 100, 50))
    out = io.BytesIO()
    image.save(out, format="BMP")
    return out.getvalue()


def test_passthrough_known_formats() -> None:
    png = _png_bytes()
    name, ctype, data = normalize_image_attachment("shot.png", "image/png", png)
    assert name == "shot.png"
    assert ctype == "image/png"
    assert data == png


def test_converts_unknown_raster_to_jpeg() -> None:
    bmp = _bmp_bytes()
    name, ctype, data = normalize_image_attachment("scan.bmp", "image/bmp", bmp)
    assert name == "scan.jpg"
    assert ctype == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (8, 8)


def test_converts_by_extension_when_mime_missing() -> None:
    bmp = _bmp_bytes()
    name, ctype, data = normalize_image_attachment(
        "photo.bmp", "application/octet-stream", bmp
    )
    assert name == "photo.jpg"
    assert ctype == "image/jpeg"
    assert data[:2] == b"\xff\xd8"


def test_leaves_non_images_alone() -> None:
    payload = b"not-an-image"
    name, ctype, data = normalize_image_attachment(
        "notes.txt", "text/plain", payload
    )
    assert name == "notes.txt"
    assert ctype == "text/plain"
    assert data == payload
