from __future__ import annotations

from dataclasses import dataclass

import anyio
import pytest

from app.services.langchain_runtime import LangChainToolExecutor, run_agentic_loop_langchain
from app.services.providers.base import ChatResponse, ChatToolCall, ChatUsage
from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec


def _usage() -> ChatUsage:
    return ChatUsage(0, 0, 0, 0, 0, 0, 0)


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
    assert tool_events[-1]["state"] == "end"
    assert tool_events[-1]["input_preview"] == '{"text": "hello"}'


@pytest.mark.asyncio
async def test_agentic_loop_keeps_attachment_data_out_of_model_and_tool_events():
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
