from __future__ import annotations

from app.core.config import (
    _normalize_azure_endpoint,
    _normalize_database_url,
    _normalize_groq_base_url,
    _normalize_openai_base_url,
)


def test_normalize_database_url_replaces_asyncpg_driver() -> None:
    url = "postgresql+asyncpg://chatui:chatui@db:5432/chatui"
    assert (
        _normalize_database_url(url)
        == "postgresql+psycopg://chatui:chatui@db:5432/chatui"
    )


def test_normalize_database_url_keeps_existing_driver() -> None:
    url = "postgresql+psycopg://chatui:chatui@db:5432/chatui"
    assert _normalize_database_url(url) == url


def test_normalize_openai_base_url_appends_v1_when_missing() -> None:
    assert _normalize_openai_base_url("https://api.openai.com") == "https://api.openai.com/v1"


def test_normalize_openai_base_url_keeps_existing_v1_suffix() -> None:
    assert _normalize_openai_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"


def test_normalize_groq_base_url_removes_openai_suffixes() -> None:
    assert _normalize_groq_base_url("https://api.groq.com/openai") == "https://api.groq.com"
    assert _normalize_groq_base_url("https://api.groq.com/openai/v1") == "https://api.groq.com"


def test_normalize_azure_endpoint_strips_known_suffixes_case_insensitive() -> None:
    assert (
        _normalize_azure_endpoint("https://foo.openai.azure.com/openai/v1")
        == "https://foo.openai.azure.com"
    )
    assert (
        _normalize_azure_endpoint("https://foo.openai.azure.com/OPENAI")
        == "https://foo.openai.azure.com"
    )
