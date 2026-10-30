from __future__ import annotations

import re

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD_ITALIC = re.compile(r"[*_~]{1,3}")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_MD_QUOTE = re.compile(r"^>\s?", re.MULTILINE)
_WS = re.compile(r"[ \t]+\n")
_MULTI_NL = re.compile(r"\n{3,}")


def plain_text_for_tts(value: str, *, max_chars: int) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = _MD_CODE_FENCE.sub(" ", text)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_LIST.sub("", text)
    text = _MD_QUOTE.sub("", text)
    text = _MD_BOLD_ITALIC.sub("", text)
    text = _WS.sub("\n", text)
    text = _MULTI_NL.sub("\n\n", text)
    text = " ".join(text.split())
    if max_chars > 0 and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
