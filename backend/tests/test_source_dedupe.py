from app.api.chats import (
    _dedupe_sources,
    _limit_sources,
    _project_sources_from_tool_output,
    _sources_cited_in_text,
)


def test_dedupe_sources_by_source_id_ignores_snippet():
    items = [
        {
            "source_id": "abc",
            "title": "agentu saraksts.xlsx",
            "snippet": "chunk one",
        },
        {
            "source_id": "abc",
            "title": "agentu saraksts.xlsx",
            "snippet": "chunk two",
        },
        {
            "source_id": "def",
            "title": "other.xlsx",
            "snippet": "other",
        },
    ]
    unique = _dedupe_sources(items)
    assert len(unique) == 2
    assert unique[0]["snippet"] == "chunk one"
    assert unique[1]["source_id"] == "def"


def test_dedupe_sources_by_url_and_title():
    items = [
        {"url": "https://example.com/a", "title": "A"},
        {"url": "https://example.com/a", "title": "A again"},
        {"title": "Local file"},
        {"title": "local file"},
        {"url": "https://example.com/b", "title": "B"},
    ]
    unique = _dedupe_sources(items)
    assert [item.get("url") or item.get("title") for item in unique] == [
        "https://example.com/a",
        "Local file",
        "https://example.com/b",
    ]


def test_project_sources_from_search_tool():
    output = {
        "results": [
            {"id": 2, "title": "audit.pdf", "snippet": "findings"},
            {"id": 2, "title": "audit.pdf", "snippet": "other chunk"},
        ]
    }
    items = _project_sources_from_tool_output("search_project_sources", output)
    assert len(items) == 2
    assert items[0]["catalog_id"] == 2
    assert items[0]["host"] == "project"
    assert items[0]["snippet"] == "findings"


def test_sources_cited_in_text_uses_registry():
    registry = {
        1: {"catalog_id": 1, "title": "A", "host": "project"},
        3: {"catalog_id": 3, "title": "C", "host": "project"},
    }
    cited = _sources_cited_in_text("Done [1] and more [3].", registry)
    assert [item["catalog_id"] for item in cited] == [1, 3]


def test_limit_sources_dedupes_without_truncating():
    items = [
        {"url": "https://example.com/a"},
        {"url": "https://example.com/a"},
        {"url": "https://example.com/b"},
        {"url": "https://example.com/c"},
        {"url": "https://example.com/d"},
        {"url": "https://example.com/e"},
        {"url": "https://example.com/f"},
    ]
    limited = _limit_sources(items)
    assert [item["url"] for item in limited] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
        "https://example.com/d",
        "https://example.com/e",
        "https://example.com/f",
    ]
