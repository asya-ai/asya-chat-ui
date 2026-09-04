from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Transport = Literal["http", "sse", "stdio"]
McpAuthType = Literal["none", "bearer", "api_token", "user_provided"]
McpScope = Literal["instance", "org", "user"]
BindingMode = Literal["inherit", "override", "disabled"]
UserAuthMethod = Literal["bearer", "api_token"]


@dataclass(frozen=True)
class McpInclude:
    tools: bool = True
    resources: bool = True
    prompts: bool = True


@dataclass(frozen=True)
class McpServerConfig:
    id: str
    name: str
    transport: Transport
    enabled: bool = True
    description: str = ""
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    include: McpInclude = field(default_factory=McpInclude)
    tool_allowlist: frozenset[str] | None = None
    tool_blocklist: frozenset[str] | None = None


@dataclass(frozen=True)
class ResolvedMcpServer:
    db_id: UUID
    config: McpServerConfig
    auth_type: McpAuthType
    auth_fingerprint: str
