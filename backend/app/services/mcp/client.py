from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from app.core.config import settings
from app.services.mcp.catalog import McpServerConfig

logger = logging.getLogger(__name__)


def _timeout() -> timedelta:
    return timedelta(seconds=max(1, int(settings.mcp_call_timeout_seconds)))


@asynccontextmanager
async def open_mcp_session(server: McpServerConfig) -> AsyncIterator[ClientSession]:
    """Open a short-lived MCP client session for the configured transport."""
    timeout = _timeout()
    if server.transport == "http":
        assert server.url
        async with streamablehttp_client(
            server.url,
            headers=server.headers or None,
            timeout=timeout,
            sse_read_timeout=timeout,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    if server.transport == "sse":
        assert server.url
        async with sse_client(
            server.url,
            headers=server.headers or None,
            timeout=timeout.total_seconds(),
            sse_read_timeout=timeout.total_seconds(),
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    if server.transport == "stdio":
        assert server.command
        params = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=server.env or None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    raise ValueError(f"Unsupported MCP transport: {server.transport}")


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {
        "type": "object",
        "properties": {},
    }
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump(mode="json")
    elif hasattr(schema, "dict"):
        schema = schema.dict()
    return {
        "name": str(getattr(tool, "name", "") or ""),
        "description": str(getattr(tool, "description", "") or ""),
        "input_schema": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
    }


def _resource_to_dict(resource: Any) -> dict[str, Any]:
    return {
        "uri": str(getattr(resource, "uri", "") or ""),
        "name": str(getattr(resource, "name", "") or ""),
        "description": str(getattr(resource, "description", "") or ""),
        "mime_type": getattr(resource, "mimeType", None) or getattr(resource, "mime_type", None),
    }


def _template_to_dict(template: Any) -> dict[str, Any]:
    return {
        "uri_template": str(
            getattr(template, "uriTemplate", None)
            or getattr(template, "uri_template", "")
            or ""
        ),
        "name": str(getattr(template, "name", "") or ""),
        "description": str(getattr(template, "description", "") or ""),
        "mime_type": getattr(template, "mimeType", None) or getattr(template, "mime_type", None),
    }


def _prompt_to_dict(prompt: Any) -> dict[str, Any]:
    arguments = []
    for arg in getattr(prompt, "arguments", None) or []:
        arguments.append(
            {
                "name": str(getattr(arg, "name", "") or ""),
                "description": str(getattr(arg, "description", "") or ""),
                "required": bool(getattr(arg, "required", False)),
            }
        )
    return {
        "name": str(getattr(prompt, "name", "") or ""),
        "description": str(getattr(prompt, "description", "") or ""),
        "arguments": arguments,
    }


async def discover_server_capabilities(server: McpServerConfig) -> dict[str, Any]:
    """List tools/resources/prompts for one server."""
    tools: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []

    async with open_mcp_session(server) as session:
        caps = session.get_server_capabilities()
        if server.include.tools:
            try:
                listed = await session.list_tools()
                tools = [_tool_to_dict(tool) for tool in (listed.tools or [])]
            except Exception as exc:
                logger.warning("MCP list_tools failed for %s: %s", server.id, exc)

        if server.include.resources:
            try:
                listed = await session.list_resources()
                resources = [_resource_to_dict(item) for item in (listed.resources or [])]
            except Exception as exc:
                logger.warning("MCP list_resources failed for %s: %s", server.id, exc)
            try:
                listed_templates = await session.list_resource_templates()
                templates = [
                    _template_to_dict(item)
                    for item in (listed_templates.resourceTemplates or [])
                ]
            except Exception as exc:
                logger.warning(
                    "MCP list_resource_templates failed for %s: %s", server.id, exc
                )

        if server.include.prompts:
            try:
                listed = await session.list_prompts()
                prompts = [_prompt_to_dict(item) for item in (listed.prompts or [])]
            except Exception as exc:
                logger.warning("MCP list_prompts failed for %s: %s", server.id, exc)

    return {
        "server": server,
        "capabilities": caps,
        "tools": tools,
        "resources": resources,
        "resource_templates": templates,
        "prompts": prompts,
    }


def content_blocks_to_json(content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if content is None:
        return blocks
    items = content if isinstance(content, list) else [content]
    for item in items:
        if isinstance(item, dict):
            blocks.append(item)
            continue
        type_name = getattr(item, "type", None) or item.__class__.__name__
        if hasattr(item, "model_dump"):
            blocks.append(item.model_dump(mode="json"))
            continue
        text = getattr(item, "text", None)
        if text is not None:
            blocks.append({"type": "text", "text": str(text)})
            continue
        data = getattr(item, "data", None)
        mime = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        if data is not None:
            blocks.append({"type": str(type_name), "mime_type": mime, "data": data})
            continue
        blocks.append({"type": str(type_name), "value": str(item)})
    return blocks


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def compact_mcp_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Collapse duplicate content/structured_content into one parsed body.

    Many MCP servers put the same JSON in both a text content block and
    structuredContent (often as a string under ``result``). Passing both to the
    model doubles token use with no extra information.
    """
    is_error = bool(payload.get("is_error"))
    content = payload.get("content")
    structured = payload.get("structured_content")

    structured = _maybe_parse_json(structured)
    if isinstance(structured, dict) and "result" in structured and len(structured) <= 2:
        structured = _maybe_parse_json(structured.get("result"))

    content_body: Any = None
    non_text_blocks: list[dict[str, Any]] = []
    if isinstance(content, list) and content:
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            else:
                non_text_blocks.append(block)
        if len(texts) == 1:
            content_body = _maybe_parse_json(texts[0])
        elif texts:
            content_body = [_maybe_parse_json(item) for item in texts]

    # Prefer structured when present; otherwise parsed text; keep raw content only
    # when we could not unwrap a body (or when there are non-text blocks).
    if structured is not None:
        body = structured
    elif content_body is not None:
        body = content_body
    else:
        body = None

    out: dict[str, Any] = {"is_error": is_error}
    if body is not None:
        out["data"] = body
    if non_text_blocks:
        out["content"] = non_text_blocks
    elif body is None and content is not None:
        out["content"] = content
    return out


def truncate_json_text(value: Any, *, max_chars: int) -> Any:
    """Soft per-string cap only — does not drop structured fields."""
    if max_chars <= 0:
        return value
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 14] + "...[truncated]"
    if isinstance(value, list):
        return [truncate_json_text(item, max_chars=max_chars) for item in value]
    if isinstance(value, dict):
        return {
            key: truncate_json_text(item, max_chars=max_chars)
            for key, item in value.items()
        }
    return value


async def call_mcp_tool(
    server: McpServerConfig, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    async with open_mcp_session(server) as session:
        result = await session.call_tool(tool_name, arguments=arguments or {})
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    payload = {
        "content": content_blocks_to_json(getattr(result, "content", None)),
        "is_error": is_error,
    }
    if structured is not None:
        if hasattr(structured, "model_dump"):
            structured = structured.model_dump(mode="json")
        payload["structured_content"] = structured
    # Deduplicate only — do not slim or hard-truncate the entity payload.
    return compact_mcp_tool_payload(payload)


async def read_mcp_resource(server: McpServerConfig, uri: str) -> dict[str, Any]:
    async with open_mcp_session(server) as session:
        result = await session.read_resource(AnyUrl(uri))
    contents = []
    for item in getattr(result, "contents", None) or []:
        if hasattr(item, "model_dump"):
            contents.append(item.model_dump(mode="json"))
        else:
            contents.append(
                {
                    "uri": str(getattr(item, "uri", "") or uri),
                    "mime_type": getattr(item, "mimeType", None)
                    or getattr(item, "mime_type", None),
                    "text": getattr(item, "text", None),
                    "blob": getattr(item, "blob", None),
                }
            )
    return truncate_json_text(
        {"uri": uri, "contents": contents},
        max_chars=settings.mcp_max_result_chars,
    )


async def get_mcp_prompt(
    server: McpServerConfig,
    name: str,
    arguments: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with open_mcp_session(server) as session:
        result = await session.get_prompt(name, arguments=arguments or {})
    messages = []
    for message in getattr(result, "messages", None) or []:
        if hasattr(message, "model_dump"):
            messages.append(message.model_dump(mode="json"))
        else:
            messages.append(
                {
                    "role": getattr(message, "role", None),
                    "content": content_blocks_to_json(getattr(message, "content", None)),
                }
            )
    return truncate_json_text(
        {
            "name": name,
            "description": getattr(result, "description", None),
            "messages": messages,
        },
        max_chars=settings.mcp_max_result_chars,
    )
