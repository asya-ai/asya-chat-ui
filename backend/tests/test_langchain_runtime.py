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
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
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