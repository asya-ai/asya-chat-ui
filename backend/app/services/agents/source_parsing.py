from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pypdf import PdfReader

MAX_EXTRACTED_CHARS = 1_000_000

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".log",
    ".ini",
    ".cfg",
}


def _trim_text(value: str) -> str:
    cleaned = re.sub(r"\r\n?", "\n", value)
    cleaned = cleaned.replace("\x00", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) > MAX_EXTRACTED_CHARS:
        return cleaned[:MAX_EXTRACTED_CHARS]
    return cleaned


def _decode_text_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode text content")


def _extract_pdf(raw: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def _extract_docx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        with archive.open("word/document.xml") as handle:
            root = ET.fromstring(handle.read())
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    return "\n".join(part for part in texts if part.strip())


def _extract_pptx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        slide_names = sorted(
            [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        )
        chunks: list[str] = []
        for name in slide_names:
            with archive.open(name) as handle:
                root = ET.fromstring(handle.read())
            texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
            slide_text = "\n".join(part for part in texts if part.strip())
            if slide_text:
                chunks.append(slide_text)
    return "\n\n".join(chunks)


def _extract_xlsx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]

        sheet_names = sorted(
            [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        )
        lines: list[str] = []
        for sheet_name in sheet_names:
            root = ET.fromstring(archive.read(sheet_name))
            for row in [node for node in root.iter() if node.tag.endswith("}row")]:
                values: list[str] = []
                for cell in [node for node in row if node.tag.endswith("}c")]:
                    value_node = next((n for n in cell if n.tag.endswith("}v")), None)
                    if value_node is None or value_node.text is None:
                        inline_node = next((n for n in cell.iter() if n.tag.endswith("}t")), None)
                        values.append((inline_node.text or "") if inline_node is not None else "")
                        continue
                    raw_value = value_node.text
                    if cell.attrib.get("t") == "s":
                        try:
                            idx = int(raw_value)
                            values.append(shared_strings[idx] if 0 <= idx < len(shared_strings) else "")
                        except ValueError:
                            values.append(raw_value)
                    else:
                        values.append(raw_value)
                if any(item.strip() for item in values):
                    lines.append("\t".join(values))
    return "\n".join(lines)


def _extract_xls(raw: bytes) -> str:
    try:
        import xlrd  # type: ignore
    except Exception as exc:
        raise ValueError("XLS support requires xlrd dependency") from exc
    workbook = xlrd.open_workbook(file_contents=raw)
    lines: list[str] = []
    for sheet in workbook.sheets():
        for row_idx in range(sheet.nrows):
            values = [str(sheet.cell_value(row_idx, col_idx)) for col_idx in range(sheet.ncols)]
            if any(value.strip() for value in values):
                lines.append("\t".join(values))
    return "\n".join(lines)


def _extract_odf(raw: bytes) -> str:
    """Extract text from Open Document Format files (.odt, .ods, .odp, etc.)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if "content.xml" not in archive.namelist():
            raise ValueError("Invalid ODF file: missing content.xml")
        root = ET.fromstring(archive.read("content.xml"))
        texts: list[str] = []
        for elem in root.iter():
            if elem.text and elem.text.strip():
                texts.append(elem.text.strip())
            if elem.tail and elem.tail.strip():
                texts.append(elem.tail.strip())
    return "\n".join(texts)


def extract_text_from_file(
    *,
    file_name: str | None,
    content_type: str | None,
    data: bytes,
) -> str:
    ext = Path((file_name or "").lower()).suffix
    mime = (content_type or "").lower()

    if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        return _trim_text(_decode_text_bytes(data))
    if ext == ".pdf" or "pdf" in mime:
        return _trim_text(_extract_pdf(data))
    if ext == ".docx":
        return _trim_text(_extract_docx(data))
    if ext == ".xlsx":
        return _trim_text(_extract_xlsx(data))
    if ext == ".xls":
        return _trim_text(_extract_xls(data))
    if ext == ".pptx":
        return _trim_text(_extract_pptx(data))
    if ext in {".odt", ".ods", ".odp", ".odg", ".odf"}:
        return _trim_text(_extract_odf(data))
    if ext in {".doc", ".ppt"}:
        raise ValueError(
            f"Legacy binary format '{ext}' is not directly supported. Please convert to modern format "
            f"('{ext}x') and upload again."
        )

    if mime.startswith(("image/", "audio/", "video/")):
        raise ValueError(
            f"Files with content type '{content_type}' cannot be indexed as text. "
            "Upload a text-based document instead."
        )

    # Unknown file types are only safe to accept when they are valid UTF-8 text.
    try:
        return _trim_text(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("Unsupported binary file format. Upload a text-based document instead.") from exc
