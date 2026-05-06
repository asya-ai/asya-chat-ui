from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.langchain_runtime import LangChainToolExecutor, run_agentic_loop_langchain
from app.services.providers.base import ChatResponse, ChatToolCall, ChatUsage
from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec


def _usage() -> ChatUsage:
    return ChatUsage(0, 0, 0, 0, 0, 0, 0)


@dataclass
class _FakeProvider:
    calls: int = 0

    async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
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
    content, attachments, sources, image_usages, usage = await run_agentic_loop_langchain(
        provider=provider,
        model_name="fake-model",
        messages=[{"role": "user", "content": "say hello"}],
        tool_registry=registry,
        max_steps=3,
    )
    assert content == "final answer"
    assert attachments == []
    assert isinstance(sources, list)
    assert image_usages == []
    assert usage is not None
