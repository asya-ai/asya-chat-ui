"""Context passed into the agentic loop for external write gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID


@dataclass
class WriteApprovalContext:
    task_id: UUID
    chat_id: UUID
    org_id: UUID
    user_id: UUID
    get_always_providers: Callable[[], set[str]]
    set_always_providers: Callable[[set[str]], None]
    set_task_awaiting: Callable[[dict[str, Any]], None] | None = None
    set_task_streaming: Callable[[], None] | None = None
