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
    tool_calls: list["ChatToolCall"] | None = None
    finish_reason: str | None = None
    sources: list[dict] | list[str] | None = None


@dataclass
class ChatStreamChunk:
    content: str | None = None
    usage: ChatUsage | None = None


class ChatProvider(Protocol):
    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        ...

    async def chat_stream(
        self, model: str, messages: list[dict]
    ) -> AsyncIterator[ChatStreamChunk]:
        ...

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list["ChatToolSpec"],
        tool_choice: object | None = None,
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