from fastapi import HTTPException
import pytest

from app.api.models import _normalize_display_name


def test_normalize_display_name_plain():
    assert _normalize_display_name("  GPT-4o  ") == "GPT-4o"


def test_normalize_display_name_group():
    assert _normalize_display_name(" OpenAI / gpt-4o ") == "OpenAI/gpt-4o"


def test_normalize_display_name_rejects_multiple_slashes():
    with pytest.raises(HTTPException) as exc:
        _normalize_display_name("a/b/c")
    assert exc.value.status_code == 400


def test_normalize_display_name_rejects_empty_sides():
    with pytest.raises(HTTPException):
        _normalize_display_name("OpenAI/")
    with pytest.raises(HTTPException):
        _normalize_display_name("/gpt")
