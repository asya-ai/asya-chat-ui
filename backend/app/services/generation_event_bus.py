from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "chatui:gen:"
# How long to wait for a pub/sub message before falling back to a DB poll.
SUBSCRIBE_TIMEOUT_SECONDS = 2.0

_sync_client: redis.Redis | None = None


def _broker_url() -> str:
    return os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")


def generation_channel(task_id: UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{task_id}"


def get_sync_redis() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.Redis.from_url(_broker_url(), decode_responses=True)
    return _sync_client


def publish_generation_event(
    task_id: UUID | str,
    *,
    sequence: int,
    event_type: str,
    payload: dict[str, Any] | None,
) -> None:
    """Best-effort notify live subscribers after an event is persisted."""
    try:
        client = get_sync_redis()
        client.publish(
            generation_channel(task_id),
            json.dumps(
                {
                    "sequence": sequence,
                    "event_type": event_type,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        logger.debug(
            "Failed to publish generation event task=%s seq=%s",
            task_id,
            sequence,
            exc_info=True,
        )


async def iter_generation_notifications(
    task_id: UUID | str,
    *,
    timeout_seconds: float = SUBSCRIBE_TIMEOUT_SECONDS,
) -> AsyncIterator[dict[str, Any] | None]:
    """
    Yield pub/sub payloads for a generation task.

    Yields ``None`` when ``timeout_seconds`` elapses without a message so callers
    can fall back to a DB catch-up poll.
    """
    client: aioredis.Redis | None = None
    pubsub: aioredis.client.PubSub | None = None
    try:
        client = aioredis.from_url(_broker_url(), decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(generation_channel(task_id))
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=timeout_seconds,
            )
            if message is None:
                yield None
                continue
            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(generation_channel(task_id))
                await pubsub.aclose()
            except Exception:
                logger.debug("Failed to close generation pubsub", exc_info=True)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("Failed to close generation redis client", exc_info=True)
