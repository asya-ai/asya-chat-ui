from app.services.tools.image_tool import (
    select_default_edit_base_attachment_id,
    _normalize_id_list,
    _is_generated_image_file_name,
)


def _entry(
    attachment_id: str,
    *,
    file_name: str | None = None,
    message_id: str = "m1",
    source: str = "chat",
    is_generated: bool | None = None,
    message_role: str = "user",
) -> dict:
    name = file_name or attachment_id
    return {
        "attachment_id": attachment_id,
        "file_name": name,
        "message_id": message_id,
        "source": source,
        "is_generated": (
            is_generated if is_generated is not None else _is_generated_image_file_name(name)
        ),
        "message_role": message_role,
        "created_at": "",
    }


def test_select_default_single_image():
    catalog = [_entry("a1", file_name="team.jpeg")]
    chosen, err = select_default_edit_base_attachment_id(catalog, latest_user_message_id="m1")
    assert err is None
    assert chosen == "a1"


def test_select_default_latest_generated_when_iterating():
    catalog = [
        _entry("u1", file_name="team.jpeg", message_id="m1", message_role="user"),
        _entry(
            "g1",
            file_name="generated-abc.jpg",
            message_id="m2",
            message_role="assistant",
        ),
    ]
    chosen, err = select_default_edit_base_attachment_id(catalog, latest_user_message_id="m3")
    assert err is None
    assert chosen == "g1"


def test_select_default_rejects_new_reference_with_existing_images():
    catalog = [
        _entry("u1", file_name="team.jpeg", message_id="m1"),
        _entry("g1", file_name="generated-abc.jpg", message_id="m2", message_role="assistant"),
        _entry("u2", file_name="portrait.jpeg", message_id="m4"),
    ]
    chosen, err = select_default_edit_base_attachment_id(catalog, latest_user_message_id="m4")
    assert chosen is None
    assert err is not None
    assert "reference" in err.lower() or "image_id" in err.lower()


def test_select_default_rejects_multiple_images_on_turn():
    catalog = [
        _entry("a1", file_name="one.png", message_id="m9"),
        _entry("a2", file_name="two.png", message_id="m9"),
    ]
    chosen, err = select_default_edit_base_attachment_id(catalog, latest_user_message_id="m9")
    assert chosen is None
    assert "Multiple images" in (err or "")


def test_select_default_rejects_ambiguous_user_only_chat():
    catalog = [
        _entry("a1", file_name="one.jpeg", message_id="m1"),
        _entry("a2", file_name="two.jpeg", message_id="m2"),
    ]
    chosen, err = select_default_edit_base_attachment_id(catalog, latest_user_message_id="m3")
    assert chosen is None
    assert "image_id" in (err or "").lower()


def test_normalize_id_list():
    assert _normalize_id_list(None) == []
    assert _normalize_id_list(" abc ") == ["abc"]
    assert _normalize_id_list(["a", "", "b"]) == ["a", "b"]
