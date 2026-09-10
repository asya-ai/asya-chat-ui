from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

import anyio
from json_repair import loads as repair_json_loads

from app.services.providers.base import ChatUsage
from app.services.tools.image_tool import image_usage_token_fields
from app.services.tools.previews import (
    tool_call_action_summary,
    tool_call_input_preview,
    tool_call_output_preview,
)
from app.services.tools.registry import ToolResult, ToolRegistry
from app.services.langchain_runtime.tool_adapters import LangChainToolExecutor
from app.services.mcp import mcp_source_items_from_tool_result

logger = logging.getLogger(__name__)

WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT = 12000
WEB_SCRAPE_ANSWER_HEAD_RATIO = 0.7

ToolEventEmitter = Callable[[dict[str, Any]], Awaitable[None]]
_tool_event_emitter: ContextVar[ToolEventEmitter | None] = ContextVar(
    "tool_event_emitter", default=None
)
_current_tool_call_id: ContextVar[str | None] = ContextVar(
    "current_tool_call_id", default=None
)
_tool_emit_lock: ContextVar[anyio.Lock | None] = ContextVar(
    "tool_emit_lock", default=None
)


async def emit_tool_event_from_tool(payload: dict[str, Any]) -> None:
    """Allow tools to push mid-flight UI events (e.g. subagent child id)."""
    emitter = _tool_event_emitter.get()
    if emitter is None:
        return
    call_id = _current_tool_call_id.get()
    if call_id and "id" not in payload:
        payload = {**payload, "id": call_id}
    lock = _tool_emit_lock.get()
    if lock is not None:
        async with lock:
            await emitter(payload)
    else:
        await emitter(payload)


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


def _strip_inline_attachment_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_inline_attachment_data(item)
            for key, item in value.items()
            if key != "data_base64"
        }
    if isinstance(value, list):
        return [_strip_inline_attachment_data(item) for item in value]
    return value


async def run_agentic_loop_langchain(
    *,
    provider: Any,
    model_name: str,
    messages: list[dict],
    tool_registry: ToolRegistry,
    max_steps: int,
    pending_attachments: list[dict] | None = None,
    activity_sender: anyio.abc.ObjectSendStream | None = None,
    tool_event_sender: anyio.abc.ObjectSendStream | None = None,
    delta_sender: anyio.abc.ObjectSendStream | None = None,
) -> tuple[str, list[dict], list[dict], list[dict], ChatUsage | None]:
    executor = LangChainToolExecutor(tool_registry)
    tool_specs = executor.list_specs()
    # Reuse the registry's pending list so mid-loop tools (e.g. extract_pdf)
    # can see attachments produced by earlier tools (e.g. download_attachments).
    attachments: list[dict] = pending_attachments if pending_attachments is not None else []
    image_usages: list[dict] = []
    usage: ChatUsage | None = None

    from app.api.chats import _dedupe_sources, _limit_sources, _source_item

    # Scraped pages first when truncating — they were deliberately visited.
    scrape_sources: list[dict] = []
    other_sources: list[dict] = []

    def _finalize_sources() -> list[dict]:
        return _limit_sources(_dedupe_sources(scrape_sources + other_sources))

    async def _emit_activity(label: str, state: str) -> None:
        if activity_sender:
            await activity_sender.send({"label": label, "state": state})

    async def _emit_tool_event(payload: dict[str, Any]) -> None:
        if tool_event_sender:
            await tool_event_sender.send(payload)

    # Tools (e.g. spawn_subagent) can emit mid-flight UI updates while running.
    _tool_event_emitter.set(_emit_tool_event)
    _tool_emit_lock.set(anyio.Lock())

    emitted_text_parts: list[str] = []

    async def _emit_delta(text: str) -> None:
        if not text:
            return
        emitted_text_parts.append(text)
        if delta_sender:
            await delta_sender.send({"delta": text})

    async def _call_model(
        current_step: int,
    ) -> tuple[str, str, list[Any], ChatUsage | None, str | None]:
        if hasattr(provider, "chat_stream_with_tools"):
            content_parts: list[str] = []
            episode_reasoning = ""
            episode_id = f"reasoning_{current_step}"
            all_reasoning_parts: list[str] = []
            tool_calls: list[Any] = []
            step_usage: ChatUsage | None = None
            finish_reason: str | None = None
            last_emitted_reasoning_len = 0

            def _absorb_reasoning_delta(piece: str) -> None:
                nonlocal episode_reasoning
                if not piece:
                    return
                # Support both true deltas and cumulative provider chunks.
                if not episode_reasoning:
                    episode_reasoning = piece
                elif piece.startswith(episode_reasoning):
                    episode_reasoning = piece
                elif episode_reasoning.endswith(piece):
                    return
                else:
                    episode_reasoning += piece

            async def _emit_reasoning(force: bool = False) -> None:
                nonlocal last_emitted_reasoning_len
                if not episode_reasoning:
                    return
                if (
                    not force
                    and last_emitted_reasoning_len > 0
                    and len(episode_reasoning) - last_emitted_reasoning_len < 80
                ):
                    return
                last_emitted_reasoning_len = len(episode_reasoning)
                await _emit_tool_event(
                    {
                        "type": "reasoning",
                        "id": episode_id,
                        "content": episode_reasoning,
                    }
                )

            async def _start_episode(next_id: str) -> None:
                nonlocal episode_id, episode_reasoning, last_emitted_reasoning_len
                if next_id == episode_id:
                    return
                await _emit_reasoning(force=True)
                if episode_reasoning:
                    all_reasoning_parts.append(episode_reasoning)
                episode_id = next_id
                episode_reasoning = ""
                last_emitted_reasoning_len = 0

            async for chunk in provider.chat_stream_with_tools(
                model_name, messages, tool_specs
            ):
                if chunk.tool_calls:
                    tool_calls = list(chunk.tool_calls)
                if chunk.content:
                    content_parts.append(chunk.content)
                    await _emit_delta(chunk.content)
                if getattr(chunk, "reasoning_content", None):
                    chunk_episode = getattr(chunk, "reasoning_id", None) or f"reasoning_{current_step}"
                    await _start_episode(chunk_episode)
                    _absorb_reasoning_delta(chunk.reasoning_content)
                    await _emit_reasoning()
                if chunk.usage:
                    step_usage = chunk.usage
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
            await _emit_reasoning(force=True)
            if episode_reasoning:
                all_reasoning_parts.append(episode_reasoning)
            return (
                "".join(content_parts),
                "\n\n".join(all_reasoning_parts),
                tool_calls,
                step_usage,
                finish_reason,
            )

        response = await provider.chat_with_tools(model_name, messages, tool_specs)
        content = response.content or ""
        reasoning_content = getattr(response, "reasoning_content", "") or ""
        tool_calls = list(response.tool_calls or [])
        if content:
            await _emit_delta(content)
        if reasoning_content:
            await _emit_tool_event({
                "type": "reasoning",
                "id": f"reasoning_{current_step}",
                "content": reasoning_content,
            })
        return (
            content,
            reasoning_content,
            tool_calls,
            response.usage,
            getattr(response, "finish_reason", None),
        )

    def _visible_content(fallback: str) -> str:
        if emitted_text_parts:
            return "".join(emitted_text_parts)
        return fallback

    def _is_length_truncated(finish_reason: str | None) -> bool:
        reason = str(finish_reason or "").strip().lower()
        return reason in {
            "length",
            "max_tokens",
            "max_output_tokens",
            "incomplete",
        }

    consecutive_empty = 0
    consecutive_length = 0
    for step in range(max_steps):
        await _emit_activity(f"Step {step + 1}/{max_steps}", "start")
        thinking_label = "Thinking"
        await _emit_activity(thinking_label, "start")
        content, reasoning_content, tool_calls, step_usage, finish_reason = await _call_model(
            step
        )
        usage = _merge_chat_usage(usage, step_usage)
        if not tool_calls:
            step_text = (content or "").strip()
            truncated = _is_length_truncated(finish_reason)
            logger.info(
                "Agentic step %s/%s no tool_calls finish_reason=%s response_len=%s",
                step + 1,
                max_steps,
                finish_reason,
                len(content or ""),
            )

            # Output was cut mid-answer — keep going instead of returning a stump.
            if truncated and step_text:
                consecutive_length += 1
                consecutive_empty = 0
                if consecutive_length >= 3:
                    logger.warning(
                        "Agentic loop stopping after %s consecutive length truncations",
                        consecutive_length,
                    )
                    await _emit_activity(thinking_label, "end")
                    await _emit_activity("Answering", "start")
                    return (
                        _visible_content(content),
                        attachments,
                        _finalize_sources(),
                        image_usages,
                        usage,
                    )
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Continue your previous response from exactly where you "
                            "left off. Do not repeat earlier text."
                        ),
                    }
                )
                await _emit_activity(thinking_label, "end")
                continue

            # Some models (esp. local/vLLM) end a tool round with empty content.
            # Nudge instead of finishing the task blank.
            if not step_text:
                consecutive_empty += 1
                consecutive_length = 0
                if consecutive_empty >= 3:
                    logger.warning(
                        "Agentic loop aborting after %s consecutive empty responses",
                        consecutive_empty,
                    )
                    await _emit_activity(thinking_label, "end")
                    await _emit_activity("Answering", "start")
                    return (
                        _visible_content(content),
                        attachments,
                        _finalize_sources(),
                        image_usages,
                        usage,
                    )
                logger.info("No tool calls and empty content; forcing final answer")
                messages.append(
                    {
                        "role": "user",
                        "content": "Please provide the final answer now.",
                    }
                )
                await _emit_activity(thinking_label, "end")
                continue

            consecutive_empty = 0
            consecutive_length = 0
            await _emit_activity(thinking_label, "end")
            await _emit_activity("Answering", "start")
            return _visible_content(content), attachments, _finalize_sources(), image_usages, usage

        consecutive_empty = 0
        consecutive_length = 0
        await _emit_activity(thinking_label, "end")
        messages.append(
            {
                "role": "assistant",
                "content": content,
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

        emit_lock = _tool_emit_lock.get() or anyio.Lock()
        _tool_emit_lock.set(emit_lock)

        async def _run_one_tool_call(call: Any) -> tuple[Any, ToolResult, dict[str, Any], ChatUsage | None]:
            call_arguments = call.arguments if isinstance(call.arguments, dict) else {}
            input_preview = tool_call_input_preview(call.name, call_arguments)
            action_summary = tool_call_action_summary(call.name, call_arguments)
            async with emit_lock:
                await _emit_activity(action_summary, "start")
                if call.name == "code_execution":
                    await _emit_tool_event(
                        {
                            "type": "code_execution",
                            "id": call.id,
                            "code": call_arguments.get("code", ""),
                            "output": {},
                        }
                    )
                elif call.name == "spawn_subagent":
                    await _emit_tool_event(
                        {
                            "type": "subagent",
                            "id": call.id,
                            "title": call_arguments.get("title"),
                            "mode": call_arguments.get("mode") or "blocking",
                            "prompt_preview": str(call_arguments.get("prompt") or "")[:240],
                            "status": "starting",
                            "output": {},
                        }
                    )
                elif call.name == "download_attachments":
                    urls = call_arguments.get("urls") or call_arguments.get("url")
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
                elif call.name == "start_coworking":
                    await _emit_tool_event(
                        {
                            "type": "coworking",
                            "id": call.id,
                            "action": "open",
                            "title": call_arguments.get("title"),
                            "file_name": call_arguments.get("file_name"),
                            "format": call_arguments.get("format"),
                            "language": call_arguments.get("language"),
                            "content": call_arguments.get("content"),
                            "output": {"status": "writing", "tool_name": call.name},
                        }
                    )
                elif call.name in {
                    "cowork_write",
                    "cowork_str_replace",
                    "cowork_append",
                }:
                    preview_content = None
                    if call.name == "cowork_write":
                        preview_content = call_arguments.get("content")
                    await _emit_tool_event(
                        {
                            "type": "coworking",
                            "id": call.id,
                            "action": "writing",
                            "content": preview_content,
                            "append_text": (
                                call_arguments.get("text")
                                if call.name == "cowork_append"
                                else None
                            ),
                            "output": {"status": "writing", "tool_name": call.name},
                        }
                    )
                # Specialized tools render their own cards — skip the generic
                # tool_call JSON dump for spawn_subagent.
                if call.name != "spawn_subagent":
                    await _emit_tool_event(
                        {
                            "type": "tool_call",
                            "id": f"call:{call.id}",
                            "tool_name": call.name,
                            "state": "start",
                            "input_preview": input_preview,
                            "action_summary": action_summary,
                            "output": {},
                        }
                    )

            result = ToolResult(name=call.name, output={})
            answer_usage: ChatUsage | None = None
            tool_output: dict[str, Any] = {}
            call_id_token = _current_tool_call_id.set(str(call.id) if call.id else None)
            try:
                result = await executor.execute(call.name, call_arguments)
                if (
                    call.name == "web_scrape"
                    and str(call_arguments.get("output") or "").strip().lower() == "answer"
                ):
                    result, answer_usage = await _normalize_web_scrape_answer_result(
                        provider=provider,
                        model_name=model_name,
                        result=result,
                    )
                raw_output = (
                    _strip_inline_attachment_data(result.output)
                    if result.attachments
                    else result.output
                )
                if isinstance(raw_output, dict):
                    tool_output = dict(raw_output)
                else:
                    tool_output = {
                        "error": "Tool returned non-object output",
                        "raw": raw_output,
                    }
                    result = ToolResult(
                        name=result.name or call.name,
                        output=tool_output,
                        attachments=result.attachments,
                    )
            except Exception as exc:
                logger.warning(
                    "Tool call failed name=%s: %s",
                    call.name,
                    exc,
                    exc_info=True,
                )
                tool_output = {"error": f"{type(exc).__name__}: {exc}"}
                result = ToolResult(name=call.name, output=tool_output)
            finally:
                _current_tool_call_id.reset(call_id_token)

            cowork_updated_payloads: list[dict[str, Any]] = []
            extra_model_usage: dict[str, Any] | None = None
            if isinstance(tool_output, dict):
                raw_payloads = tool_output.pop("_cowork_updated_payloads", None)
                if isinstance(raw_payloads, list):
                    cowork_updated_payloads = [
                        item for item in raw_payloads if isinstance(item, dict)
                    ]
                raw_tool_usage = tool_output.pop("_tool_usage", None)
                if isinstance(raw_tool_usage, dict) and int(raw_tool_usage.get("total_tokens") or 0):
                    extra_model_usage = raw_tool_usage
                if isinstance(tool_output.get("cowork_updated"), list):
                    tool_output["cowork_updated"] = [
                        {
                            "document_id": item.get("document_id"),
                            "file_name": item.get("file_name"),
                            "version": item.get("version"),
                            "synced_path": item.get("synced_path"),
                        }
                        for item in tool_output["cowork_updated"]
                        if isinstance(item, dict)
                    ]
                # Keep ToolResult in sync with model-facing output (strip internal fields).
                result = ToolResult(
                    name=result.name or call.name,
                    output=tool_output,
                    attachments=result.attachments,
                )

            if (
                call.name in {"generate_image", "edit_image"}
                and isinstance(tool_output, dict)
                and not tool_output.get("error")
            ):
                file_name = tool_output.get("file_name")
                if isinstance(file_name, str) and file_name.strip():
                    tool_output["note"] = (
                        f"To embed in a Marp presentation or markdown document, reference "
                        f"this exact file_name: ![bg right:40% cover]({file_name}) or "
                        f"![]({file_name}). The app resolves chat attachment names; do not "
                        "invent other URLs or paths."
                    )
                    result = ToolResult(
                        name=result.name or call.name,
                        output=tool_output,
                        attachments=result.attachments,
                    )

            error_text = tool_output.get("error")
            if error_text is None and tool_output.get("is_error"):
                error_text = "Tool reported an error"
            is_error = error_text is not None
            result_preview = tool_call_output_preview(call.name, tool_output)

            async with emit_lock:
                if call.name == "code_execution":
                    await _emit_tool_event(
                        {
                            "type": "code_execution",
                            "id": call.id,
                            "code": call_arguments.get("code", ""),
                            "output": tool_output,
                        }
                    )
                    for payload in cowork_updated_payloads:
                        await _emit_tool_event(
                            {
                                "type": "coworking",
                                "id": call.id,
                                "action": payload.get("action") or "update",
                                "document_id": payload.get("document_id"),
                                "title": payload.get("title"),
                                "file_name": payload.get("file_name"),
                                "format": payload.get("format"),
                                "language": payload.get("language"),
                                "version": payload.get("version"),
                                "last_assistant_version": payload.get(
                                    "last_assistant_version"
                                ),
                                "user_edited": payload.get("user_edited"),
                                "content": payload.get("content"),
                                "output": {
                                    "status": "ok",
                                    "tool_name": "code_execution",
                                    "synced": True,
                                },
                            }
                        )
                elif call.name == "spawn_subagent":
                    await _emit_tool_event(
                        {
                            "type": "subagent",
                            "id": call.id,
                            "title": tool_output.get("title")
                            or call_arguments.get("title"),
                            "mode": tool_output.get("mode")
                            or call_arguments.get("mode")
                            or "blocking",
                            "child_chat_id": tool_output.get("child_chat_id"),
                            "task_id": tool_output.get("task_id"),
                            "status": tool_output.get("status")
                            or ("error" if is_error else "completed"),
                            "summary": tool_output.get("summary"),
                            "prompt_preview": str(call_arguments.get("prompt") or "")[:240],
                            "output": tool_output,
                        }
                    )
                elif call.name == "download_attachments":
                    urls = call_arguments.get("urls") or call_arguments.get("url")
                    if isinstance(urls, str):
                        urls = [urls]
                    await _emit_tool_event(
                        {
                            "type": "url_attachments",
                            "id": call.id,
                            "urls": urls if isinstance(urls, list) else [],
                            "output": tool_output,
                        }
                    )
                elif call.name in {
                    "start_coworking",
                    "cowork_write",
                    "cowork_str_replace",
                    "cowork_append",
                }:
                    action = "open" if call.name == "start_coworking" else "update"
                    if isinstance(tool_output, dict) and not tool_output.get("error"):
                        event = {
                            "type": "coworking",
                            "id": call.id,
                            "action": tool_output.get("action") or action,
                            "document_id": tool_output.get("document_id"),
                            "title": tool_output.get("title"),
                            "file_name": tool_output.get("file_name"),
                            "format": tool_output.get("format"),
                            "language": tool_output.get("language"),
                            "version": tool_output.get("version"),
                            "last_assistant_version": tool_output.get(
                                "last_assistant_version"
                            ),
                            "user_edited": tool_output.get("user_edited"),
                            "content": tool_output.get("content"),
                            "output": {
                                "status": "ok",
                                "tool_name": call.name,
                            },
                        }
                        await _emit_tool_event(event)
                    elif isinstance(tool_output, dict) and tool_output.get("error"):
                        await _emit_tool_event(
                            {
                                "type": "coworking",
                                "id": call.id,
                                "action": action,
                                "output": {
                                    "status": "error",
                                    "error": tool_output.get("error"),
                                    "tool_name": call.name,
                                },
                            }
                        )
                if call.name != "spawn_subagent":
                    await _emit_tool_event(
                        {
                            "type": "tool_call",
                            "id": f"call:{call.id}",
                            "tool_name": call.name,
                            "state": "end",
                            "input_preview": input_preview,
                            "action_summary": action_summary,
                            "output": {
                                "status": "error" if is_error else "ok",
                                "result_preview": result_preview,
                                "raw_output": tool_output,
                                "error": str(error_text) if error_text is not None else None,
                                "attachments": [
                                    {
                                        "file_name": item.get("file_name"),
                                        "content_type": item.get("content_type"),
                                        "data_base64": item.get("data_base64"),
                                    }
                                    for item in (result.attachments or [])
                                    if isinstance(item, dict) and item.get("data_base64")
                                ]
                                or None,
                            },
                        }
                    )
            return call, result, tool_output, answer_usage, extra_model_usage

        call_results: list[
            tuple[Any, ToolResult, dict[str, Any], ChatUsage | None, dict[str, Any] | None] | None
        ] = [
            None
        ] * len(tool_calls)

        async def _store_tool_result(index: int, call: Any) -> None:
            call_results[index] = await _run_one_tool_call(call)

        async with anyio.create_task_group() as tg:
            for index, call in enumerate(tool_calls):
                tg.start_soon(_store_tool_result, index, call)

        for item in call_results:
            assert item is not None
            call, result, tool_output, answer_usage, extra_model_usage = item
            usage = _merge_chat_usage(usage, answer_usage)
            if result.attachments:
                attachments.extend(result.attachments)
            if extra_model_usage:
                image_usages.append(extra_model_usage)
            if call.name in {"generate_image", "edit_image"}:
                model_id = result.output.get("model_id") if isinstance(result.output, dict) else None
                if model_id:
                    image_usages.append(
                        {
                            "model_id": model_id,
                            **image_usage_token_fields(
                                result.output if isinstance(result.output, dict) else None
                            ),
                            "image_width": result.output.get("image_width"),
                            "image_height": result.output.get("image_height"),
                            "image_count": result.output.get("image_count"),
                            "image_format": result.output.get("image_format"),
                        }
                    )
            if call.name == "web_search":
                queries = result.output.get("queries", []) or []
                if isinstance(queries, list):
                    for query_result in queries:
                        if not isinstance(query_result, dict):
                            continue
                        for item in query_result.get("results", []) or []:
                            if not isinstance(item, dict):
                                continue
                            url = item.get("url")
                            if isinstance(url, str) and url.strip():
                                other_sources.append(_source_item(url, item.get("title")))
            elif call.name == "web_scrape":
                scrape_results = result.output.get("results", []) or []
                if isinstance(scrape_results, list):
                    for item in scrape_results:
                        if not isinstance(item, dict):
                            continue
                        url = item.get("url")
                        if isinstance(url, str) and url.strip():
                            scrape_sources.append(_source_item(url, item.get("title")))
            elif call.name == "search_past_chats":
                chat_results = result.output.get("results", []) or []
                if isinstance(chat_results, list):
                    for item in chat_results:
                        if not isinstance(item, dict):
                            continue
                        cid = item.get("chat_id")
                        if cid:
                            other_sources.append(
                                _source_item(f"/chat/{cid}", item.get("chat_title"))
                            )
            else:
                for source in mcp_source_items_from_tool_result(call.name, result.output):
                    other_sources.append(source)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(tool_output, ensure_ascii=False),
                }
            )

    return (
        "I reached the tool step limit before finishing. Please refine your request and try again.",
        attachments,
        _finalize_sources(),
        image_usages,
        usage,
    )
