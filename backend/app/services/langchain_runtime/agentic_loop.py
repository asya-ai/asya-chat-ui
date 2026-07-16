from __future__ import annotations

import json
from typing import Any

import anyio
from json_repair import loads as repair_json_loads

from app.services.providers.base import ChatUsage
from app.services.tools.registry import ToolResult, ToolRegistry
from app.services.langchain_runtime.tool_adapters import LangChainToolExecutor

WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT = 12000
WEB_SCRAPE_ANSWER_HEAD_RATIO = 0.7


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


def _parse_web_answer_payload(content: str) -> tuple[str, list[str], bool]:
    try:
        data = repair_json_loads(content)
    except Exception:
        return content.strip(), [], False
    if not isinstance(data, dict):
        return content.strip(), [], False
    answer = str(data.get("answer") or "").strip()
    quotes_raw = data.get("quotes")
    quotes = [str(item).strip() for item in quotes_raw if str(item).strip()] if isinstance(quotes_raw, list) else []
    insufficient = bool(data.get("insufficient_information"))
    return answer, quotes, insufficient


def _build_markdown_context(markdown: str) -> str:
    if len(markdown) <= WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT:
        return markdown
    head_len = int(WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT * WEB_SCRAPE_ANSWER_HEAD_RATIO)
    head_len = max(head_len, 1)
    tail_len = max(WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT - head_len, 1)
    return (
        markdown[:head_len].rstrip()
        + "\n\n[... source content omitted for length ...]\n\n"
        + markdown[-tail_len:].lstrip()
    )


async def _generate_web_scrape_answer(
    *,
    provider: Any,
    model_name: str,
    result_item: dict[str, Any],
) -> tuple[dict[str, Any], ChatUsage | None]:
    analysis_input = result_item.get("analysis_input")
    if not isinstance(analysis_input, dict):
        return result_item, None

    normalized: dict[str, Any] = {
        key: value for key, value in result_item.items() if key != "analysis_input"
    }
    question = str(result_item.get("question") or "").strip()
    if not question:
        normalized["error"] = "Question missing for output=answer"
        return normalized, None

    markdown = str(analysis_input.get("markdown") or "").strip()
    if not markdown:
        normalized["error"] = "No markdown extracted for answering"
        return normalized, None
    markdown = _build_markdown_context(markdown)

    instructions = (
        "Analyze the provided website content and answer the question.\n"
        f'Question: "{question}"\n\n'
        "Rules:\n"
        "1) Use only the provided source material (markdown and screenshot if present).\n"
        "2) If the source material is insufficient, say so clearly.\n"
        "3) Do not use prior/personal knowledge.\n"
        "4) Return strict JSON with keys: answer (string), insufficient_information (boolean), quotes (array of strings).\n"
        "5) quotes must contain direct quotes from the source, or be [] when insufficient."
    )
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": instructions + "\n\nSource markdown:\n" + markdown,
        }
    ]
    screenshot_base64 = str(analysis_input.get("screenshot_base64") or "")
    screenshot_content_type = (
        str(analysis_input.get("screenshot_content_type") or "image/png").strip()
        or "image/png"
    )
    include_image = bool(screenshot_base64)
    if include_image:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{screenshot_content_type};base64,{screenshot_base64}"
                },
            }
        )
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are a strict evidence-grounded website analyzer. "
                "Never rely on outside knowledge."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    try:
        response = await provider.chat(model_name, prompt_messages)
    except Exception:
        if not include_image:
            raise
        prompt_messages[1] = {"role": "user", "content": user_content[:1]}
        response = await provider.chat(model_name, prompt_messages)
    answer, quotes, insufficient = _parse_web_answer_payload(response.content or "")
    normalized["answer"] = answer
    normalized["quotes"] = quotes
    normalized["insufficient_information"] = insufficient
    if insufficient:
        normalized["error"] = answer or "Source material was insufficient"
    if analysis_input.get("screenshot_error") and not analysis_input.get("screenshot_base64"):
        normalized["note"] = "Screenshot could not be used; answer is based on markdown only."
    return normalized, response.usage


async def _normalize_web_scrape_answer_result(
    *,
    provider: Any,
    model_name: str,
    result: ToolResult,
) -> tuple[ToolResult, ChatUsage | None]:
    scrape_results = result.output.get("results")
    if not isinstance(scrape_results, list):
        return result, None

    normalized_results: list[dict[str, Any]] = []
    additional_usage: ChatUsage | None = None
    for item in scrape_results:
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            normalized_results.append(
                {key: value for key, value in item.items() if key != "analysis_input"}
            )
            continue
        try:
            answered_item, answer_usage = await _generate_web_scrape_answer(
                provider=provider,
                model_name=model_name,
                result_item=item,
            )
            normalized_results.append(answered_item)
            additional_usage = _merge_chat_usage(additional_usage, answer_usage)
        except Exception as exc:
            normalized_results.append(
                {
                    **{key: value for key, value in item.items() if key != "analysis_input"},
                    "error": f"Answer generation failed: {exc}",
                }
            )

    result.output["results"] = normalized_results
    return result, additional_usage


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
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "thought_signature": call.thought_signature,
                    }
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            input_preview = json.dumps(call.arguments, ensure_ascii=False)[:200]
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
                    "input_preview": input_preview,
                    "output": {},
                }
            )
            result: ToolResult = await executor.execute(call.name, call.arguments)
            if (
                call.name == "web_scrape"
                and str((call.arguments or {}).get("output") or "").strip().lower() == "answer"
            ):
                result, answer_usage = await _normalize_web_scrape_answer_result(
                    provider=provider,
                    model_name=model_name,
                    result=result,
                )
                usage = _merge_chat_usage(usage, answer_usage)
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
                    "input_preview": input_preview,
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
