from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, TypeVar

import anyio
import httpx
from ddgs import DDGS
from ddgs import http_client as ddgs_http_client

from app.core.config import settings
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
    if not language or not country:
        return None
    if country == "gb":
        country = "uk"
    return f"{country}-{language}"


async def web_search(context: WebToolContext, *, query: str | None = None, queries: list[str] | None = None, max_results: int | None = None) -> ToolResult:
    query_list = _ensure_list(queries) or _ensure_list(query)
    if not query_list:
        return ToolResult(
            name="web_search",
            output={"error": "No query provided"},
        )
    limit = min(max_results or settings.web_search_limit, settings.web_search_limit, 10)
    parallel_limit = settings.scrape_parallel_max
    region = _locale_to_region(context.locale)

    async def _search_one(item: str) -> dict:
        def _run() -> list[dict]:
            with DDGS() as ddgs:
                if region:
                    return list(ddgs.text(item, max_results=limit, region=region))
                return list(ddgs.text(item, max_results=limit))

        rows = await anyio.to_thread.run_sync(_run)
        results = [
            {
                "title": row.get("title"),
                "url": row.get("href"),
                "snippet": row.get("body"),
            }
            for row in rows
        ]
        return {"query": item, "results": results}

    logger.info("web_search org_id=%s queries=%s", context.org_id, len(query_list))
    batches = await _run_parallel(query_list, parallel_limit, _search_one)
    logger.info(
        "web_search done org_id=%s results=%s",
        context.org_id,
        sum(len(batch.get("results", []) or []) for batch in batches if isinstance(batch, dict)),
    )
    return ToolResult(name="web_search", output={"queries": batches})


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


async def web_scrape(context: WebToolContext, *, url: str | None = None, urls: list[str] | None = None) -> ToolResult:
    url_list = _ensure_list(urls) or _ensure_list(url)
    if not url_list:
        return ToolResult(name="web_scrape", output={"error": "No URL provided"})
    url_list = url_list[:3]
    if not settings.scraper_url:
        return ToolResult(name="web_scrape", output={"error": "Scraper URL not configured"})

    parallel_limit = settings.scrape_parallel_max
    text_limit = settings.scrape_text_limit

    async def _scrape_one(item: str) -> dict:
        if not item.startswith(("http://", "https://")):
            return {"url": item, "error": "Invalid URL scheme"}
        parsed = urlparse(item)
        hostname = parsed.hostname
        if not hostname or _is_private_hostname(hostname):
            return {"url": item, "error": "Blocked host"}
        payload = {"url": item}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{settings.scraper_url}/scrape", json=payload
                )
                if response.status_code >= 400:
                    return {"url": item, "error": f"Scrape failed ({response.status_code})"}
                data = response.json()
                markdown = data.get("markdown", "") or ""
                if len(markdown) > text_limit:
                    markdown = markdown[:text_limit]
                return {
                    "url": data.get("finalUrl") or item,
                    "title": data.get("title"),
                    "markdown": markdown,
                }
        except Exception as exc:
            logger.warning("web_scrape error url=%s err=%s", item, exc)
            return {"url": item, "error": str(exc)}

    logger.info("web_scrape org_id=%s urls=%s", context.org_id, len(url_list))
    results = await _run_parallel(url_list, parallel_limit, _scrape_one)
    logger.info(
        "web_scrape done org_id=%s results=%s",
        context.org_id,
        sum(1 for item in results if isinstance(item, dict) and item.get("markdown")),
    )
    return ToolResult(name="web_scrape", output={"results": results})
