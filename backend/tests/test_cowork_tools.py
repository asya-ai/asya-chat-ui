import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.models.entities import Chat, ChatCoworkDocument, Org, User
from app.services.tools.cowork_tools import (
    CoworkToolContext,
    apply_user_patch,
    build_user_edit_context_message,
    cowork_append,
    cowork_read,
    cowork_str_replace,
    cowork_write,
    start_coworking,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        org = Org(name="Test Org")
        session.add(org)
        session.commit()
        session.refresh(org)
        user = User(
            email="cowork@example.com",
            hashed_password="x",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        chat = Chat(org_id=org.id, user_id=user.id, title="Cowork")
        session.add(chat)
        session.commit()
        session.refresh(chat)
        yield session, chat


@pytest.mark.asyncio
async def test_start_and_str_replace(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    started = await start_coworking(
        ctx,
        title="Hello",
        format="markdown",
        file_name="hello.md",
        content="# Title\n\nHello world\n",
    )
    assert started.output["status"] == "started"
    assert started.output["file_name"] == "hello.md"

    replaced = await cowork_str_replace(
        ctx, old_str="Hello world", new_str="Hello cowork"
    )
    assert replaced.output.get("error") is None
    assert "Hello cowork" in replaced.output["content"]
    assert replaced.output["version"] == 2
    assert replaced.output["last_assistant_version"] == 2


@pytest.mark.asyncio
async def test_str_replace_requires_unique_match(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    await start_coworking(ctx, content="foo\nfoo\n", format="text", file_name="a.txt")
    result = await cowork_str_replace(ctx, old_str="foo", new_str="bar")
    assert "matched" in (result.output.get("error") or "").lower()

    all_replaced = await cowork_str_replace(
        ctx, old_str="foo", new_str="bar", replace_all=True
    )
    assert all_replaced.output.get("error") is None
    assert all_replaced.output["content"] == "bar\nbar\n"


@pytest.mark.asyncio
async def test_user_edit_diff_injection(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    started = await start_coworking(
        ctx, content="line1\n", format="text", file_name="notes.txt"
    )
    doc_id = started.output["document_id"]
    doc = db.get(ChatCoworkDocument, __import__("uuid").UUID(doc_id))
    assert doc is not None
    updated, _ = apply_user_patch(db, doc, content="line1\nline2\n", base_version=1)
    assert updated is not None
    note = build_user_edit_context_message(updated)
    assert note is not None
    assert "User edited" in note
    assert "line2" in note


@pytest.mark.asyncio
async def test_write_and_append_and_line_range_read(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    await start_coworking(ctx, content="", format="code", language="python", title="app")
    written = await cowork_write(ctx, content="def a():\n    return 1\n")
    assert written.output["file_name"].endswith(".py")
    appended = await cowork_append(ctx, text="\ndef b():\n    return 2\n")
    assert "def b" in appended.output["content"]
    partial = await cowork_read(ctx, offset=0, limit=1)
    assert partial.output["content"].startswith("def a")
    assert partial.output["has_more"] is True


@pytest.mark.asyncio
async def test_start_presentation_marp(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    deck = (
        "---\ntheme: default\n---\n\n# Hello\n\n---\n\n## Slide 2\n"
    )
    started = await start_coworking(
        ctx,
        title="Pitch",
        format="presentation",
        content=deck,
    )
    assert started.output["status"] == "started"
    assert started.output["format"] == "presentation"
    assert started.output["file_name"].endswith(".md")
    assert started.output["language"] is None
    assert "# Hello" in started.output["content"]


@pytest.mark.asyncio
async def test_list_documents_stable_by_created_at(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    first = await start_coworking(ctx, title="First", format="text", content="a")
    second = await start_coworking(ctx, title="Second", format="text", content="b")
    from app.services.tools.cowork_tools import list_documents

    docs = list_documents(db, chat.id)
    assert [d.title for d in docs] == ["First", "Second"]

    # Activating older doc must not reorder the list.
    from app.services.tools.cowork_tools import activate_document
    from uuid import UUID

    activate_document(db, chat.id, UUID(first.output["document_id"]))
    docs_after = list_documents(db, chat.id)
    assert [d.title for d in docs_after] == ["First", "Second"]
    assert docs_after[0].is_active is True


@pytest.mark.asyncio
async def test_delete_document_activates_neighbor(session):
    db, chat = session
    ctx = CoworkToolContext(session=db, chat_id=chat.id)
    first = await start_coworking(ctx, title="First", format="text", content="a")
    second = await start_coworking(ctx, title="Second", format="text", content="b")
    third = await start_coworking(ctx, title="Third", format="text", content="c")
    from uuid import UUID

    from app.services.tools.cowork_tools import delete_document, list_documents

    # Delete the active (third) → previous (second) becomes active
    active = delete_document(db, chat.id, UUID(third.output["document_id"]))
    assert active is not None
    assert active.title == "Second"
    assert [d.title for d in list_documents(db, chat.id)] == ["First", "Second"]

    # Delete non-active first → second stays active
    active2 = delete_document(db, chat.id, UUID(first.output["document_id"]))
    assert active2 is not None
    assert active2.title == "Second"
    assert [d.title for d in list_documents(db, chat.id)] == ["Second"]

    # Delete last remaining → none
    active3 = delete_document(db, chat.id, UUID(second.output["document_id"]))
    assert active3 is None
    assert list_documents(db, chat.id) == []
