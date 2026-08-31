from __future__ import annotations

from dataclasses import dataclass

import anyio
import pytest

from app.services.langchain_runtime import LangChainToolExecutor, run_agentic_loop_langchain
from app.services.providers.base import ChatResponse, ChatToolCall, ChatUsage
from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec


def _usage(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    thinking_tokens: int = 0,
) -> ChatUsage:
    return ChatUsage(
        prompt_tokens,
        completion_tokens,
        total_tokens,
        input_tokens,
        output_tokens,
        cached_tokens,
        thinking_tokens,
    )


@dataclass
class _FakeProvider:
    calls: int = 0
    seen_messages: list[list[dict]] | None = None

    async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
        if self.seen_messages is None:
            self.seen_messages = []
        self.seen_messages.append(messages.copy())
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                content="",
                usage=_usage(),
                tool_calls=[
                    ChatToolCall(
                        id="tool-1",
                        name="echo_tool",
                        arguments={"text": "hello"},
                    )
                ],
            )
        return ChatResponse(content="final answer", usage=_usage(), tool_calls=[])


@dataclass
class _ThoughtSignatureProvider:
    calls: int = 0
    seen_messages: list[list[dict]] | None = None

    async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
        if self.seen_messages is None:
            self.seen_messages = []
        self.seen_messages.append(messages.copy())
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                content="",
                usage=_usage(),
                tool_calls=[
                    ChatToolCall(
                        id="call-1",
                        name="echo_tool",
                        arguments={"text": "hello"},
                        thought_signature="sig-abc123",
                    )
                ],
            )
        return ChatResponse(content="done", usage=_usage(), tool_calls=[])


@dataclass
class _WebScrapeAnswerProvider:
    calls: int = 0
    seen_messages: list[list[dict]] | None = None
    answer_messages: list[list[dict]] | None = None

    async def chat(self, model: str, messages: list[dict]):
        if self.answer_messages is None:
            self.answer_messages = []
        self.answer_messages.append(messages.copy())
        return ChatResponse(
            content='{"answer":"Use mean 0.5","insufficient_information":false,"quotes":["mean 0.5"],}',
            usage=_usage(),
        )

    async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
        if self.seen_messages is None:
            self.seen_messages = []
        self.seen_messages.append(messages.copy())
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                content="",
                usage=_usage(),
                tool_calls=[
                    ChatToolCall(
                        id="scrape-1",
                        name="web_scrape",
                        arguments={
                            "url": "https://example.com",
                            "output": "answer",
                            "question": "What mean does it use?",
                        },
                    )
                ],
            )
        return ChatResponse(content="final answer", usage=_usage(), tool_calls=[])


@pytest.mark.asyncio
async def test_langchain_tool_executor_executes_registry_tool():
    registry = ToolRegistry()

    async def _echo(args: dict) -> ToolResult:
        return ToolResult(name="echo_tool", output={"echo": args.get("text")})

    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _echo,
    )
    executor = LangChainToolExecutor(registry)
    result = await executor.execute("echo_tool", {"text": "hello"})
    assert result.output == {"echo": "hello"}


@pytest.mark.asyncio
async def test_langchain_executor_accepts_leading_underscore_schema_fields():
    registry = ToolRegistry()
    seen: dict = {}

    async def _capture(args: dict) -> ToolResult:
        seen.update(args)
        return ToolResult(name="data-lv__ask_pipeworx", output={"ok": True})

    registry.register(
        ToolSpec(
            name="data-lv__ask_pipeworx",
            description="Ask",
            parameters={
                "type": "object",
                "properties": {
                    "_apiKey": {"type": "string", "description": "secret"},
                    "question": {"type": "string"},
                },
                "required": ["question"],
            },
        ),
        _capture,
    )
    executor = LangChainToolExecutor(registry)
    result = await executor.execute(
        "data-lv__ask_pipeworx",
        {"_apiKey": "tok", "question": "population?"},
    )
    assert result.output == {"ok": True}
    assert seen == {"_apiKey": "tok", "question": "population?"}


def test_json_schema_to_model_sanitizes_leading_underscores():
    from app.services.langchain_runtime.tool_adapters import _json_schema_to_model

    model, field_map = _json_schema_to_model(
        "data-lv__tool",
        {
            "type": "object",
            "properties": {
                "_apiKey": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    )
    assert "field_apiKey" in model.model_fields
    assert "query" in model.model_fields
    assert field_map["field_apiKey"] == "_apiKey"
    assert field_map["query"] == "query"


@pytest.mark.asyncio
async def test_agentic_loop_langchain_runs_tool_then_returns_final_answer():
    registry = ToolRegistry()

    async def _echo(args: dict) -> ToolResult:
        return ToolResult(name="echo_tool", output={"echo": args.get("text")})

    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _echo,
    )
    provider = _FakeProvider()
    tool_event_sender, tool_event_receiver = anyio.create_memory_object_stream(4)
    content, attachments, sources, image_usages, usage = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "say hello"}],
        tool_registry=registry,
        max_steps=3,
        tool_event_sender=tool_event_sender,
    )
    await tool_event_sender.aclose()
    tool_events = [event async for event in tool_event_receiver]

    assert content == "final answer"
    assert attachments == []
    assert isinstance(sources, list)
    assert image_usages == []
    assert usage is not None
    assert usage.total_tokens == 0
    assert tool_events[-1]["state"] == "end"
    assert tool_events[-1]["input_preview"] == '{"text": "hello"}'
    assert tool_events[-1]["action_summary"] == "Running echo tool"
    assert tool_events[-1]["output"]["result_preview"] == "completed"

@pytest.mark.asyncio
async def test_agentic_loop_shares_pending_attachments_with_later_tools():
    """download_attachments → extract_pdf must see the same pending list mid-loop."""
    pending_attachments: list[dict] = []
    registry = ToolRegistry()
    seen_pending_counts: list[int] = []

    async def _download(_args: dict) -> ToolResult:
        return ToolResult(
            name="download_attachments",
            output={"results": [{"file_name": "manual.pdf", "content_type": "application/pdf"}]},
            attachments=[
                {
                    "file_name": "manual.pdf",
                    "content_type": "application/pdf",
                    "data_base64": "cGRm",
                }
            ],
        )

    async def _extract(_args: dict) -> ToolResult:
        seen_pending_counts.append(len(pending_attachments))
        return ToolResult(
            name="extract_pdf",
            output={
                "file_name": pending_attachments[0]["file_name"] if pending_attachments else None,
                "error": None if pending_attachments else "PDF attachment not found for this chat.",
            },
        )

    registry.register(
        ToolSpec(
            name="download_attachments",
            description="Download files",
            parameters={"type": "object", "properties": {"url": {"type": "string"}}},
        ),
        _download,
    )
    registry.register(
        ToolSpec(
            name="extract_pdf",
            description="Extract PDF",
            parameters={"type": "object", "properties": {}},
        ),
        _extract,
    )

    @dataclass
    class _DownloadThenExtractProvider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[
                        ChatToolCall(
                            id="dl-1",
                            name="download_attachments",
                            arguments={"url": "https://example.com/manual.pdf"},
                        )
                    ],
                )
            if self.calls == 2:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[ChatToolCall(id="pdf-1", name="extract_pdf", arguments={})],
                )
            return ChatResponse(content="done", usage=_usage(), tool_calls=[])

    content, attachments, _sources, _image_usages, usage = await run_agentic_loop_langchain(
        provider=_DownloadThenExtractProvider(),
        model_name="fake-model",
        messages=[{"role": "user", "content": "read the manual"}],
        tool_registry=registry,
        max_steps=4,
        pending_attachments=pending_attachments,
    )

    assert content == "done"
    assert seen_pending_counts == [1]
    assert attachments is pending_attachments
    assert len(attachments) == 1
    assert attachments[0]["file_name"] == "manual.pdf"
    assert usage is not None


@pytest.mark.asyncio
async def test_agentic_loop_merges_usage_across_model_steps_and_tool_answers():
    registry = ToolRegistry()

    async def _web_scrape(args: dict) -> ToolResult:
        return ToolResult(
            name="web_scrape",
            output={
                "results": [
                    {
                        "url": "https://example.com",
                        "question": "What is it?",
                        "analysis_input": {"markdown": "Example Domain"},
                    }
                ]
            },
        )

    registry.register(
        ToolSpec(
            name="web_scrape",
            description="Scrape a page",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "output": {"type": "string"},
                },
                "required": ["url"],
            },
        ),
        _web_scrape,
    )

    @dataclass
    class _CountingProvider:
        calls: int = 0
        answer_calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(
                        prompt_tokens=10,
                        completion_tokens=2,
                        total_tokens=12,
                        input_tokens=10,
                        output_tokens=2,
                    ),
                    tool_calls=[
                        ChatToolCall(
                            id="tool-1",
                            name="web_scrape",
                            arguments={"url": "https://example.com", "output": "answer"},
                        )
                    ],
                )
            return ChatResponse(
                content="final answer",
                usage=_usage(
                    prompt_tokens=20,
                    completion_tokens=5,
                    total_tokens=25,
                    input_tokens=18,
                    output_tokens=5,
                    cached_tokens=2,
                ),
                tool_calls=[],
            )

        async def chat(self, model: str, messages: list[dict]):
            self.answer_calls += 1
            return ChatResponse(
                content='{"answer":"Example Domain","insufficient_information":false,"quotes":["Example Domain"]}',
                usage=_usage(
                    prompt_tokens=7,
                    completion_tokens=3,
                    total_tokens=10,
                    input_tokens=7,
                    output_tokens=3,
                    thinking_tokens=1,
                ),
            )

    provider = _CountingProvider()
    content, attachments, sources, image_usages, usage = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "scrape it"}],
        tool_registry=registry,
        max_steps=3,
    )

    assert content == "final answer"
    assert provider.calls == 2
    assert provider.answer_calls == 1
    assert image_usages == []
    assert usage is not None
    assert usage.prompt_tokens == 37
    assert usage.completion_tokens == 10
    assert usage.total_tokens == 47
    assert usage.input_tokens == 35
    assert usage.output_tokens == 10
    assert usage.cached_tokens == 2
    assert usage.thinking_tokens == 1


@pytest.mark.asyncio
async def test_agentic_loop_records_image_usages_from_image_tools():
    registry = ToolRegistry()

    async def _generate_image(args: dict) -> ToolResult:
        return ToolResult(
            name="generate_image",
            output={
                "model_id": "11111111-1111-1111-1111-111111111111",
                "prompt_tokens": 19,
                "completion_tokens": 1056,
                "total_tokens": 1075,
                "input_tokens": 19,
                "output_tokens": 1056,
                "cached_tokens": 0,
                "thinking_tokens": 0,
                "image_width": 1024,
                "image_height": 1024,
                "image_count": 1,
                "image_format": "png",
            },
            attachments=[
                {
                    "file_name": "generated.png",
                    "content_type": "image/png",
                    "data_base64": "abc",
                }
            ],
        )

    registry.register(
        ToolSpec(
            name="generate_image",
            description="Generate an image",
            parameters={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        ),
        _generate_image,
    )

    @dataclass
    class _ImageProvider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(total_tokens=4, input_tokens=3, output_tokens=1, prompt_tokens=3, completion_tokens=1),
                    tool_calls=[
                        ChatToolCall(
                            id="tool-1",
                            name="generate_image",
                            arguments={"prompt": "a cat"},
                        )
                    ],
                )
            return ChatResponse(
                content="done",
                usage=_usage(total_tokens=6, input_tokens=5, output_tokens=1, prompt_tokens=5, completion_tokens=1),
                tool_calls=[],
            )

    provider = _ImageProvider()
    content, attachments, sources, image_usages, usage = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "draw a cat"}],
        tool_registry=registry,
        max_steps=3,
    )

    assert content == "done"
    assert len(attachments) == 1
    assert image_usages == [
        {
            "model_id": "11111111-1111-1111-1111-111111111111",
            "prompt_tokens": 19,
            "completion_tokens": 1056,
            "total_tokens": 1075,
            "input_tokens": 19,
            "output_tokens": 1056,
            "cached_tokens": 0,
            "thinking_tokens": 0,
            "image_width": 1024,
            "image_height": 1024,
            "image_count": 1,
            "image_format": "png",
        }
    ]
    assert usage is not None
    assert usage.total_tokens == 10


@pytest.mark.asyncio
async def test_agentic_loop_records_perplexity_usage_from_web_search():
    registry = ToolRegistry()

    async def _web_search(args: dict) -> ToolResult:
        return ToolResult(
            name="web_search",
            output={
                "queries": [
                    {
                        "query": "cats near me",
                        "results": [{"url": "https://example.com", "title": "Cats"}],
                    }
                ],
                "_tool_usage": {
                    "provider": "perplexity",
                    "model_name": "sonar-pro",
                    "display_name": "Perplexity sonar-pro",
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "cached_tokens": 0,
                    "thinking_tokens": 0,
                },
            },
        )

    registry.register(
        ToolSpec(
            name="web_search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        ),
        _web_search,
    )

    @dataclass
    class _SearchProvider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(total_tokens=4, input_tokens=3, output_tokens=1, prompt_tokens=3, completion_tokens=1),
                    tool_calls=[
                        ChatToolCall(
                            id="tool-1",
                            name="web_search",
                            arguments={"query": "cats near me"},
                        )
                    ],
                )
            return ChatResponse(
                content="done",
                usage=_usage(total_tokens=6, input_tokens=5, output_tokens=1, prompt_tokens=5, completion_tokens=1),
                tool_calls=[],
            )

    provider = _SearchProvider()
    content, _attachments, _sources, image_usages, usage = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "find cats"}],
        tool_registry=registry,
        max_steps=3,
    )

    assert content == "done"
    assert image_usages == [
        {
            "provider": "perplexity",
            "model_name": "sonar-pro",
            "display_name": "Perplexity sonar-pro",
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "input_tokens": 100,
            "output_tokens": 40,
            "cached_tokens": 0,
            "thinking_tokens": 0,
        }
    ]
    assert usage is not None
    assert usage.total_tokens == 10


@pytest.mark.asyncio
async def test_agentic_loop_strips_attachment_data_from_model_keeps_tool_event_payload():
    registry = ToolRegistry()
    image_base64 = "large-image-payload"

    async def _echo(args: dict) -> ToolResult:
        return ToolResult(
            name="echo_tool",
            output={
                "file_name": "generated.png",
                "data_base64": image_base64,
                "files": [{"data_base64": image_base64}],
            },
            attachments=[
                {
                    "file_name": "generated.png",
                    "content_type": "image/png",
                    "data_base64": image_base64,
                }
            ],
        )

    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Return an attachment",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _echo,
    )
    provider = _FakeProvider()
    tool_event_sender, tool_event_receiver = anyio.create_memory_object_stream(4)

    _, attachments, *_ = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "make an image"}],
        tool_registry=registry,
        max_steps=3,
        tool_event_sender=tool_event_sender,
    )
    await tool_event_sender.aclose()
    tool_events = [event async for event in tool_event_receiver]

    assert attachments[0]["data_base64"] == image_base64
    assert provider.seen_messages is not None
    tool_message = next(
        message
        for message in provider.seen_messages[1]
        if message.get("role") == "tool"
    )
    assert image_base64 not in tool_message["content"]
    assert "data_base64" not in tool_events[-1]["output"]["raw_output"]
    assert tool_events[-1]["output"]["raw_output"]["files"] == [{}]
    assert tool_events[-1]["output"]["attachments"] == [
        {
            "file_name": "generated.png",
            "content_type": "image/png",
            "data_base64": image_base64,
        }
    ]


@pytest.mark.asyncio
async def test_agentic_loop_preserves_thought_signature_in_tool_roundtrip():
    registry = ToolRegistry()

    async def _echo(args: dict) -> ToolResult:
        return ToolResult(name="echo_tool", output={"echo": args.get("text")})

    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _echo,
    )
    provider = _ThoughtSignatureProvider()
    content, *_ = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "say hello"}],
        tool_registry=registry,
        max_steps=3,
    )

    assert content == "done"
    assert provider.seen_messages is not None
    assert len(provider.seen_messages) >= 2
    second_call_messages = provider.seen_messages[1]
    assistant_tool_message = next(
        msg for msg in second_call_messages if msg.get("role") == "assistant" and msg.get("tool_calls")
    )
    assert assistant_tool_message["tool_calls"][0]["thought_signature"] == "sig-abc123"


@pytest.mark.asyncio
async def test_agentic_loop_runs_multiple_tool_calls_in_parallel():
    registry = ToolRegistry()
    started = anyio.Event()
    release = anyio.Event()
    active = 0
    max_active = 0
    lock = anyio.Lock()

    async def _slow_echo(args: dict) -> ToolResult:
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
            if active >= 2:
                started.set()
        await release.wait()
        async with lock:
            active -= 1
        return ToolResult(name="echo_tool", output={"echo": args.get("text")})

    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _slow_echo,
    )

    @dataclass
    class _ParallelProvider:
        calls: int = 0
        seen_messages: list[list[dict]] | None = None

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            if self.seen_messages is None:
                self.seen_messages = []
            self.seen_messages.append(messages.copy())
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[
                        ChatToolCall(id="tool-1", name="echo_tool", arguments={"text": "one"}),
                        ChatToolCall(id="tool-2", name="echo_tool", arguments={"text": "two"}),
                    ],
                )
            return ChatResponse(content="done", usage=_usage(), tool_calls=[])

    provider = _ParallelProvider()
    results: list[str] = []

    async def _run() -> None:
        content, *_ = await run_agentic_loop_langchain(
            provider=provider,
            model_name="fake-model",
            messages=[{"role": "user", "content": "run both"}],
            tool_registry=registry,
            max_steps=3,
        )
        results.append(content)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        with anyio.fail_after(2):
            await started.wait()
        release.set()

    assert results == ["done"]
    assert max_active >= 2
    assert provider.seen_messages is not None
    tool_messages = [
        message
        for message in provider.seen_messages[1]
        if message.get("role") == "tool"
    ]
    assert [message["tool_call_id"] for message in tool_messages] == ["tool-1", "tool-2"]


@pytest.mark.asyncio
async def test_agentic_loop_normalizes_web_scrape_answer_before_roundtrip():
    registry = ToolRegistry()
    huge_screenshot = "a" * 1_000_000

    async def _web_scrape(args: dict) -> ToolResult:
        return ToolResult(
            name="web_scrape",
            output={
                "results": [
                    {
                        "url": args.get("url"),
                        "title": "Example",
                        "output": "answer",
                        "question": args.get("question"),
                        "analysis_input": {
                            "markdown": "The mean is 0.5.",
                            "screenshot_base64": huge_screenshot,
                            "screenshot_content_type": "image/png",
                        },
                    }
                ]
            },
        )

    registry.register(
        ToolSpec(
            name="web_scrape",
            description="Scrape a page",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "output": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["url"],
            },
        ),
        _web_scrape,
    )
    provider = _WebScrapeAnswerProvider()
    content, *_ = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "read the page"}],
        tool_registry=registry,
        max_steps=3,
    )

    assert content == "final answer"
    assert provider.seen_messages is not None
    assert provider.answer_messages is not None
    answer_content = provider.answer_messages[0][1]["content"]
    assert answer_content[1]["type"] == "image_url"
    assert huge_screenshot in answer_content[1]["image_url"]["url"]
    second_call_messages = provider.seen_messages[1]
    tool_message = next(msg for msg in second_call_messages if msg.get("role") == "tool")
    assert "analysis_input" not in tool_message["content"]
    assert huge_screenshot not in tool_message["content"]
    assert "Use mean 0.5" in tool_message["content"]


@dataclass
class _StreamingToolsProvider:
    calls: int = 0

    async def chat_stream_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
        from app.services.providers.base import ChatStreamChunk

        self.calls += 1
        if self.calls == 1:
            yield ChatStreamChunk(content="Looking that up. ")
            yield ChatStreamChunk(finish_reason="tool_calls")
            yield ChatStreamChunk(
                usage=_usage(),
                tool_calls=[
                    ChatToolCall(
                        id="tool-1",
                        name="echo_tool",
                        arguments={"text": "hello"},
                    )
                ],
                finish_reason="tool_calls",
            )
            return
        yield ChatStreamChunk(content="final ")
        yield ChatStreamChunk(content="answer")
        yield ChatStreamChunk(usage=_usage(), tool_calls=None, finish_reason="stop")


@pytest.mark.asyncio
async def test_agentic_loop_streams_final_answer_deltas():
    registry = ToolRegistry()

    async def _echo(args: dict) -> ToolResult:
        return ToolResult(name="echo_tool", output={"echo": args.get("text")})

    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _echo,
    )
    provider = _StreamingToolsProvider()
    delta_sender, delta_receiver = anyio.create_memory_object_stream(8)
    content, *_ = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "say hello"}],
        tool_registry=registry,
        max_steps=3,
        delta_sender=delta_sender,
    )
    await delta_sender.aclose()
    deltas = [event async for event in delta_receiver]

    assert content == "Looking that up. final answer"
    assert [item["delta"] for item in deltas] == [
        "Looking that up. ",
        "final ",
        "answer",
    ]


@pytest.mark.asyncio
async def test_agentic_loop_tracks_search_result_and_scrape_urls():
    registry = ToolRegistry()

    async def _web_search(args: dict) -> ToolResult:
        return ToolResult(
            name="web_search",
            output={
                "queries": [
                    {
                        "query": "NOT GREEN matcha Latvia",
                        "answer": "summary",
                        "results": [
                            {"url": "https://search-hit.example/a", "title": "Hit A"},
                            {"url": "https://search-hit.example/b", "title": "Hit B"},
                            {"url": "https://search-hit.example/c", "title": "Hit C"},
                            {"url": "https://search-hit.example/d", "title": "Hit D"},
                            {"url": "https://search-hit.example/e", "title": "Hit E"},
                        ],
                    }
                ]
            },
        )

    async def _web_scrape(args: dict) -> ToolResult:
        return ToolResult(
            name="web_scrape",
            output={
                "results": [
                    {"url": "https://scraped.example/page", "title": "Scraped Page"},
                    {"url": "https://scraped.example/other", "title": "Other Page"},
                ]
            },
        )

    registry.register(
        ToolSpec(
            name="web_search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _web_search,
    )
    registry.register(
        ToolSpec(
            name="web_scrape",
            description="Scrape a page",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        _web_scrape,
    )

    @dataclass
    class _SearchThenScrapeProvider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(total_tokens=1, input_tokens=1, output_tokens=0, prompt_tokens=1),
                    tool_calls=[
                        ChatToolCall(
                            id="search-1",
                            name="web_search",
                            arguments={"query": "NOT GREEN matcha Latvia"},
                        ),
                        ChatToolCall(
                            id="scrape-1",
                            name="web_scrape",
                            arguments={"url": "https://scraped.example/page"},
                        ),
                    ],
                )
            return ChatResponse(
                content="final answer",
                usage=_usage(
                    total_tokens=2,
                    input_tokens=1,
                    output_tokens=1,
                    prompt_tokens=1,
                    completion_tokens=1,
                ),
            )

    content, _attachments, sources, _image_usages, usage = await run_agentic_loop_langchain(
        provider=_SearchThenScrapeProvider(),
        model_name="fake-model",
        messages=[{"role": "user", "content": "research this"}],
        tool_registry=registry,
        max_steps=3,
    )

    assert content == "final answer"
    assert usage is not None
    urls = [item.get("url") for item in sources]
    titles = [item.get("title") for item in sources]
    assert "NOT GREEN matcha Latvia" not in titles
    assert "https://scraped.example/page" in urls
    assert "https://scraped.example/other" in urls
    # Scrapes stay first; search hits are kept (no truncation).
    assert urls[:2] == [
        "https://scraped.example/page",
        "https://scraped.example/other",
    ]
    assert len(sources) == 7
    assert set(urls) == {
        "https://scraped.example/page",
        "https://scraped.example/other",
        "https://search-hit.example/a",
        "https://search-hit.example/b",
        "https://search-hit.example/c",
        "https://search-hit.example/d",
        "https://search-hit.example/e",
    }
