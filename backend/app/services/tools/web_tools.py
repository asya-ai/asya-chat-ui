from __future__ import annotations

import ipaddress
import logging
import base64
import mimetypes
import os
import socket
from urllib.parse import urlparse
from urllib.parse import unquote
from dataclasses import dataclass
from typing import Any, TypeVar

import anyio
import httpx
from ddgs import DDGS
from ddgs import http_client as ddgs_http_client

from app.core.config import settings
from app.services.tool_usage import merge_tool_usage_fields, perplexity_usage_fields
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

_SUPPORTED_IMPERSONATES = (
    "chrome_144",
    "chrome_145",
    "edge_144",
    "edge_145",
    "opera_126",
    "opera_127",
    "safari_18.5",
    "safari_26",
    "firefox_140",
    "firefox_146",
)
_SUPPORTED_IMPERSONATE_OS = ("android", "ios", "linux", "macos", "windows")

# Keep ddgs in sync with primp-supported impersonations to avoid warnings.
ddgs_http_client.HttpClient._impersonates = _SUPPORTED_IMPERSONATES
ddgs_http_client.HttpClient._impersonates_os = _SUPPORTED_IMPERSONATE_OS


@dataclass
class WebToolContext:
    org_id: str
    locale: str | None = None


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


T = TypeVar("T")


async def _run_parallel(
    items: list[T], limit: int, func
) -> list[Any]:
    if not items:
        return []
    semaphore = anyio.Semaphore(limit)
    results: list[Any] = [None] * len(items)

    async def _worker(idx: int, item: T) -> None:
        async with semaphore:
            try:
                results[idx] = await func(item)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("web_tool worker error: %s", exc)
                results[idx] = {"error": str(exc)}

    async with anyio.create_task_group() as tg:
        for idx, item in enumerate(items):
            tg.start_soon(_worker, idx, item)
    return results


def _locale_to_region(locale: str | None) -> str | None:
    if not locale:
        return None
    value = locale.replace("_", "-").strip()
    if not value:
        return None
    parts = value.split("-")
    language = parts[0].lower() if parts else ""
    country = parts[1].lower() if len(parts) > 1 else ""
    if not language:
        return None
    if not country:
        defaults = {
            "en": "us-en",
            "lv": "lv-lv",
            "ja": "jp-ja",
        }
        return defaults.get(language)
    if country == "gb":
        country = "uk"
    return f"{country}-{language}"


async def _perplexity_search_one(item: str, limit: int) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.perplexity_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.perplexity_model or "sonar-pro",
                "messages": [{"role": "user", "content": item}],
            },
        )
        response.raise_for_status()
        data = response.json()
    answer = ""
    if data.get("choices"):
        answer = data["choices"][0].get("message", {}).get("content", "")
    citations = data.get("citations", [])
    results = [{"url": url} for url in citations[:limit]]
    usage = data.get("usage")
    payload: dict[str, Any] = {
        "query": item,
        "answer": answer,
        "sources": citations,
        "results": results,
    }
    if isinstance(usage, dict):
        payload["_usage"] = perplexity_usage_fields(usage)
    return payload


async def _ddgs_search_one(item: str, limit: int, region: str | None) -> dict:
    _backends = ["duckduckgo", "brave", "startpage"]

    def _run() -> list[dict]:
        for backend in _backends:
            try:
                with DDGS(timeout=8) as ddgs:
                    rows = list(ddgs.text(
                        item,
                        max_results=limit,
                        region=region or "us-en",
                        backend=backend,
                    ))
                if rows:
                    return rows
            except Exception as exc:
                logger.debug("DDGS backend %s failed: %s", backend, exc)
                continue
        return []

    with anyio.fail_after(30):
        rows = await anyio.to_thread.run_sync(_run, abandon_on_cancel=True)
    results = [
        {
            "title": row.get("title"),
            "url": row.get("href"),
            "snippet": row.get("body"),
        }
        for row in rows
    ]
    return {"query": item, "results": results}


async def web_search(context: WebToolContext, *, query: str | None = None, queries: list[str] | None = None, max_results: int | None = None) -> ToolResult:
    query_list = _ensure_list(queries) or _ensure_list(query)
    if not query_list:
        return ToolResult(
            name="web_search",
            output={"error": "No query provided"},
        )
    limit = min(max_results or settings.web_search_limit, settings.web_search_limit, 10)
    parallel_limit = settings.scrape_parallel_max
    perplexity_available = bool(settings.perplexity_api_key)
    region = _locale_to_region(context.locale)

    if perplexity_available:
        try:
            probe = await _perplexity_search_one(query_list[0], limit)
            perplexity_ok = True
        except Exception as exc:
            logger.warning("Perplexity probe failed, using DDGS for all queries: %s", exc)
            probe = None
            perplexity_ok = False
    else:
        probe = None
        perplexity_ok = False

    async def _search_one(item: str) -> dict:
        if perplexity_ok:
            try:
                return await _perplexity_search_one(item, limit)
            except Exception as exc:
                logger.warning("Perplexity search failed for query, falling back to DDGS: %s", exc)
        return await _ddgs_search_one(item, limit, region)

    logger.info(
        "web_search org_id=%s queries=%s provider=%s",
        context.org_id,
        len(query_list),
        "perplexity" if perplexity_ok else "ddgs",
    )
    remaining = query_list[1:] if probe is not None else query_list
    batches = await _run_parallel(remaining, parallel_limit, _search_one)
    if probe is not None:
        batches = [probe] + batches
    logger.info(
        "web_search done org_id=%s results=%s",
        context.org_id,
        sum(len(batch.get("results", []) or []) for batch in batches if isinstance(batch, dict)),
    )
    output: dict[str, Any] = {"queries": []}
    perplexity_usage: dict[str, int] = {key: 0 for key in perplexity_usage_fields({})}
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        batch_usage = batch.pop("_usage", None)
        if isinstance(batch_usage, dict):
            merge_tool_usage_fields(perplexity_usage, batch_usage)
        output["queries"].append(batch)
    if perplexity_ok and perplexity_usage.get("total_tokens"):
        model_name = settings.perplexity_model or "sonar-pro"
        output["_tool_usage"] = {
            "provider": "perplexity",
            "model_name": model_name,
            "display_name": f"Perplexity {model_name}",
            **perplexity_usage,
        }
    return ToolResult(name="web_search", output=output)


def _is_private_hostname(hostname: str) -> bool:
    if hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if hostname.endswith((".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return not ip.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return True
    return False


def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name).strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base).strip("._")
    return cleaned or "download"


def _filename_from_response(url: str, headers: httpx.Headers, content_type: str | None) -> str:
    content_disposition = headers.get("content-disposition", "")
    if "filename=" in content_disposition:
        candidate = content_disposition.split("filename=", 1)[1].strip().strip('"')
        if candidate:
            return _sanitize_filename(unquote(candidate))
    parsed = urlparse(url)
    path_name = _sanitize_filename(unquote(parsed.path.rsplit("/", 1)[-1] or ""))
    if "." in path_name:
        return path_name
    guessed_ext = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) or ""
    return _sanitize_filename(f"{path_name or 'download'}{guessed_ext}")


def _looks_like_blocked_page(title: str | None, markdown: str | None) -> str | None:
    title_text = (title or "").strip().lower()
    markdown_text = (markdown or "").strip().lower()
    compact = " ".join(markdown_text.split())
    if title_text in {"access denied", "forbidden", "not authorized", "robot check"}:
        return title or "Access denied"
    blocked_phrases = (
        "access denied",
        "you don't have permission to access",
        "request blocked",
        "enable javascript and cookies",
        "verify you are human",
        "are you a robot",
        "captcha",
    )
    if any(phrase in compact for phrase in blocked_phrases) and len(compact) < 2000:
        return "Page appears to be blocked or access denied"
    return None


async def web_scrape(
    context: WebToolContext,
    *,
    url: str | None = None,
    urls: list[str] | None = None,
    output: str | None = None,
    question: str | None = None,
) -> ToolResult:
    url_list = _ensure_list(urls) or _ensure_list(url)
    if not url_list:
        return ToolResult(name="web_scrape", output={"error": "No URL provided"})
    url_list = url_list[:3]
    if not settings.scraper_url:
        return ToolResult(name="web_scrape", output={"error": "Scraper URL not configured"})

    parallel_limit = settings.scrape_parallel_max
    text_limit = settings.scrape_text_limit
    output_mode = (output or "markdown").strip().lower()
    if output_mode not in {"markdown", "screenshot", "answer"}:
        output_mode = "markdown"
    question_text = (question or "").strip()
    if output_mode == "answer" and not question_text:
        return ToolResult(
            name="web_scrape",
            output={"error": "Question is required when output=answer"},
        )

    def _estimate_bytes(value: str) -> int:
        padding = value.count("=")
        return max(len(value) * 3 // 4 - padding, 0)

    async def _call_scraper(item: str, mode: str) -> tuple[dict[str, Any] | None, str | None]:
        payload = {"url": item, "output": mode}
        # Scraper may settle SPAs for ~20s+; keep client timeout above that.
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.scraper_url}/scrape", json=payload
            )
        if response.status_code >= 400:
            detail = ""
            try:
                data = response.json()
                if isinstance(data, dict):
                    detail = str(data.get("detail") or data.get("error") or "").strip()
            except Exception:
                detail = (response.text or "").strip()
            error = f"Scrape failed ({response.status_code})"
            if detail:
                error = f"{error}: {detail[:500]}"
            logger.warning(
                "web_scrape upstream failed url=%s mode=%s status=%s detail=%s",
                item,
                mode,
                response.status_code,
                detail[:500] if detail else "",
            )
            return None, error
        return response.json(), None

    async def _scrape_one(item: str) -> dict:
        if not item.startswith(("http://", "https://")):
            return {"url": item, "error": "Invalid URL scheme"}
        parsed = urlparse(item)
        hostname = parsed.hostname
        if not hostname or _is_private_hostname(hostname):
            return {"url": item, "error": "Blocked host"}
        try:
            if output_mode == "answer":
                markdown_data, markdown_error = await _call_scraper(item, "markdown")
                if markdown_error or not markdown_data:
                    return {"url": item, "error": markdown_error or "Failed to scrape markdown"}
                screenshot_data, screenshot_error = await _call_scraper(item, "screenshot")
                base_output = {
                    "url": markdown_data.get("finalUrl") or item,
                    "title": markdown_data.get("title"),
                    "output": "answer",
                    "question": question_text,
                }
                markdown = markdown_data.get("markdown", "") or ""
                if len(markdown) > text_limit:
                    markdown = markdown[:text_limit]
                if not str(markdown).strip():
                    return {
                        **base_output,
                        "error": "Scrape returned empty content",
                        "blocked": False,
                    }
                blocked_reason = _looks_like_blocked_page(
                    markdown_data.get("title"), markdown
                )
                if blocked_reason:
                    return {
                        **base_output,
                        "error": blocked_reason,
                        "blocked": True,
                    }
                screenshot_base64 = ""
                screenshot_available = False
                if screenshot_data and not screenshot_error:
                    screenshot_base64 = screenshot_data.get("screenshot", "") or ""
                if screenshot_base64:
                    if _estimate_bytes(screenshot_base64) > settings.attachments_max_file_bytes:
                        screenshot_base64 = ""
                    else:
                        screenshot_available = True
                return {
                    **base_output,
                    "analysis_input": {
                        "markdown": markdown,
                        "screenshot_base64": screenshot_base64,
                        "screenshot_content_type": "image/png",
                        "screenshot_available": screenshot_available,
                        "screenshot_error": screenshot_error,
                    },
                }

            data, error = await _call_scraper(item, output_mode)
            if error or not data:
                return {"url": item, "error": error or "Scrape failed"}

            base_output = {
                "url": data.get("finalUrl") or item,
                "title": data.get("title"),
            }
            if output_mode == "screenshot":
                screenshot_base64 = data.get("screenshot", "") or ""
                if not screenshot_base64:
                    return {**base_output, "error": "Screenshot missing"}
                if _estimate_bytes(screenshot_base64) > settings.attachments_max_file_bytes:
                    return {**base_output, "error": "Screenshot exceeds maximum size"}
                attachments = [
                    {
                        "file_name": "screenshot.png",
                        "content_type": "image/png",
                        "data_base64": screenshot_base64,
                    }
                ]
                return {**base_output, "output": "screenshot", "attachments": attachments}
            markdown = data.get("markdown", "") or ""
            if len(markdown) > text_limit:
                markdown = markdown[:text_limit]
            if not str(markdown).strip():
                return {
                    **base_output,
                    "error": "Scrape returned empty content",
                    "output": "markdown",
                }
            blocked_reason = _looks_like_blocked_page(data.get("title"), markdown)
            if blocked_reason:
                return {
                    **base_output,
                    "error": blocked_reason,
                    "blocked": True,
                    "output": "markdown",
                }
            return {**base_output, "markdown": markdown, "output": "markdown"}
        except Exception as exc:
            logger.warning("web_scrape error url=%s err=%s", item, exc)
            return {"url": item, "error": str(exc)}

    logger.info(
        "web_scrape org_id=%s urls=%s output=%s question=%s",
        context.org_id,
        len(url_list),
        output_mode,
        question_text if output_mode == "answer" else "",
    )
    results = await _run_parallel(url_list, parallel_limit, _scrape_one)
    success_count = sum(
        1
        for item in results
        if isinstance(item, dict) and not item.get("error")
    )
    logger.info(
        "web_scrape done org_id=%s output=%s success=%s total=%s",
        context.org_id,
        output_mode,
        success_count,
        len(results),
    )
    attachments: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        item_attachments = item.pop("attachments", None)
        if isinstance(item_attachments, list):
            attachments.extend(
                [
                    attachment
                    for attachment in item_attachments
                    if isinstance(attachment, dict)
                ]
            )
    return ToolResult(
        name="web_scrape",
        output={"results": results},
        attachments=attachments or None,
    )


async def download_attachments(
    context: WebToolContext,
    *,
    url: str | None = None,
    urls: list[str] | None = None,
) -> ToolResult:
    url_list = _ensure_list(urls) or _ensure_list(url)
    if not url_list:
        return ToolResult(name="download_attachments", output={"error": "No URL provided"})
    url_list = url_list[:25]

    async def _download_one(item: str) -> dict[str, Any]:
        if not item.startswith(("http://", "https://")):
            return {"url": item, "error": "Invalid URL scheme"}
        parsed = urlparse(item)
        hostname = parsed.hostname
        if not hostname or _is_private_hostname(hostname):
            return {"url": item, "error": "Blocked host"}
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(item)
            if response.status_code >= 400:
                return {"url": item, "error": f"Download failed ({response.status_code})"}
            final_hostname = urlparse(str(response.url)).hostname
            if final_hostname and _is_private_hostname(final_hostname):
                return {"url": item, "error": "Blocked redirect host"}
            content_type = (response.headers.get("content-type") or "application/octet-stream").split(";", 1)[0].strip()
            if content_type == "text/html":
                return {"url": item, "error": "URL points to an HTML page, not a direct file"}
            data = response.content or b""
            if not data:
                return {"url": item, "error": "Downloaded file is empty"}
            if len(data) > settings.attachments_max_file_bytes:
                return {
                    "url": item,
                    "error": f"File exceeds size limit ({len(data)} bytes)",
                }
            file_name = _filename_from_response(str(response.url), response.headers, content_type)
            return {
                "url": str(response.url),
                "file_name": file_name,
                "content_type": content_type,
                "size_bytes": len(data),
                "attachment_data": data,
            }
        except Exception as exc:
            logger.warning("download_attachments error url=%s err=%s", item, exc)
            return {"url": item, "error": str(exc)}

    logger.info("download_attachments org_id=%s urls=%s", context.org_id, len(url_list))
    results = await _run_parallel(url_list, settings.scrape_parallel_max, _download_one)
    attachments: list[dict[str, Any]] = []
    normalized_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        data = item.pop("attachment_data", None)
        if isinstance(data, (bytes, bytearray)):
            attachments.append(
                {
                    "file_name": item.get("file_name") or "download",
                    "content_type": item.get("content_type") or "application/octet-stream",
                    "data_base64": base64.b64encode(bytes(data)).decode("ascii"),
                }
            )
        normalized_results.append(item)
    return ToolResult(
        name="download_attachments",
        output={"results": normalized_results},
        attachments=attachments or None,
    )
