from app.api.chats import _dedupe_sources, _limit_sources


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
