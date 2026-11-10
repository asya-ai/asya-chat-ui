from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


@dataclass
class ChatUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int


@dataclass
class ChatResponse:
    content: str
    usage: ChatUsage
    reasoning_content: str | None = None
    tool_calls: list["ChatToolCall"] | None = None
    finish_reason: str | None = None
    sources: list[dict] | list[str] | None = None
    raw_completion: Any | None = None


@dataclass
class ChatStreamChunk:
    content: str | None = None
    reasoning_content: str | None = None
    # When set, scopes which Thoughts action this delta updates (new id => new action).
    reasoning_id: str | None = None
    usage: ChatUsage | None = None
    tool_calls: list["ChatToolCall"] | None = None
    tool_call_deltas: list[dict[str, Any]] | None = None
    finish_reason: str | None = None


class ChatProvider(Protocol):
    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict[str, Any] | None = None,
    ) -> ChatResponse:
        ...

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        ...

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list["ChatToolSpec"],
        tool_choice: object | None = None,
        *,
        completion_params: dict[str, Any] | None = None,
    ) -> ChatResponse:
        ...


@dataclass
class ChatToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ChatToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = None