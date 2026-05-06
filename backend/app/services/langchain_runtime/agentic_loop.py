from __future__ import annotations

import json
from typing import Any

import anyio

from app.services.providers.base import ChatUsage
from app.services.tools.registry import ToolResult, ToolRegistry
from app.services.langchain_runtime.tool_adapters import LangChainToolExecutor


def _merge_chat_usage(base: ChatUsage | None, extra: ChatUsage | None) -> ChatUsage | None:
    if base is None:
        return extra
    if extra is None:
        return base
    return ChatUsage(
        prompt_tokens=base.prompt_tokens + extra.prompt_tokens,
        completion_tokens=base.completion_tokens + extra.completion_tokens,
        total_tokens=base.total_tokens + extra.total_tokens,
        input_tokens=base.input_tokens + extra.input_tokens,
        output_tokens=base.output_tokens + extra.output_tokens,
        cached_tokens=base.cached_tokens + extra.cached_tokens,
        thinking_tokens=base.thinking_tokens + extra.thinking_tokens,
    )


async def run_agentic_loop_langchain(
    *,
    provider: Any,
    model_name: str,
    messages: list[dict],
    tool_registry: ToolRegistry,
    max_steps: int,
    activity_sender: anyio.abc.ObjectSendStream | None = None,
    tool_event_sender: anyio.abc.ObjectSendStream | None = None,
) -> tuple[str, list[dict], list[dict], list[dict], ChatUsage | None]:
    executor = LangChainToolExecutor(tool_registry)
    tool_specs = executor.list_specs()
    attachments: list[dict] = []
    sources: list[dict] = []
    image_usages: list[dict] = []
    usage: ChatUsage | None = None

    async def _emit_activity(label: str, state: str) -> None:
        if activity_sender:
            await activity_sender.send({"label": label, "state": state})

    async def _emit_tool_event(payload: dict[str, Any]) -> None:
        if tool_event_sender:
            await tool_event_sender.send(payload)

    for step in range(max_steps):
        await _emit_activity(f"Step {step + 1}/{max_steps}", "start")
        response = await provider.chat_with_tools(model_name, messages, tool_specs)
        usage = _merge_chat_usage(usage, response.usage)
        tool_calls = response.tool_calls or []
        if not tool_calls:
            await _emit_activity("Answering", "start")
            return response.content or "", attachments, sources, image_usages, usage

        messages.append(
            {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            if call.name == "code_execution":
                await _emit_tool_event(
                    {
                        "type": "code_execution",
                        "id": call.id,
                        "code": (call.arguments or {}).get("code", ""),
                        "output": {},
                    }
                )
            elif call.name == "download_attachments":
                urls = (call.arguments or {}).get("urls") or (call.arguments or {}).get("url")
                if isinstance(urls, str):
                    urls = [urls]
                await _emit_tool_event(
                    {
                        "type": "url_attachments",
                        "id": call.id,
                        "urls": urls if isinstance(urls, list) else [],
                        "output": {},
                    }
                )
            await _emit_tool_event(
                {
                    "type": "tool_call",
                    "id": f"call:{call.id}",
                    "tool_name": call.name,
                    "state": "start",
                    "input_preview": json.dumps(call.arguments, ensure_ascii=False)[:200],
                    "output": {},
                }
            )
            result: ToolResult = await executor.execute(call.name, call.arguments)
            if result.attachments:
                attachments.extend(result.attachments)
            if call.name in {"web_search", "web_scrape"}:
                result_sources = result.output.get("queries") or result.output.get("results")
                if isinstance(result_sources, list):
                    for item in result_sources:
                        if isinstance(item, dict):
                            sources.append(
                                {
                                    "title": item.get("title") or item.get("query"),
                                    "url": item.get("url"),
                                    "snippet": item.get("snippet") or item.get("answer"),
                                }
                            )
            if call.name == "code_execution":
                await _emit_tool_event(
                    {
                        "type": "code_execution",
                        "id": call.id,
                        "code": (call.arguments or {}).get("code", ""),
                        "output": result.output,
                    }
                )
            elif call.name == "download_attachments":
                urls = (call.arguments or {}).get("urls") or (call.arguments or {}).get("url")
                if isinstance(urls, str):
                    urls = [urls]
                await _emit_tool_event(
                    {
                        "type": "url_attachments",
                        "id": call.id,
                        "urls": urls if isinstance(urls, list) else [],
                        "output": result.output,
                    }
                )
            await _emit_tool_event(
                {
                    "type": "tool_call",
                    "id": f"call:{call.id}",
                    "tool_name": call.name,
                    "state": "end",
                    "output": {
                        "status": "error" if result.output.get("error") else "ok",
                        "result_preview": json.dumps(result.output, ensure_ascii=False)[:240],
                        "raw_output": result.output,
                        "error": result.output.get("error"),
                    },
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result.output, ensure_ascii=False),
                }
            )

    return (
        "I reached the tool step limit before finishing. Please refine your request and try again.",
        attachments,
        sources,
        image_usages,
        usage,
    )
