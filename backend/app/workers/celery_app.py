from __future__ import annotations

import asyncio
import logging
import os

from celery import Celery
from celery.signals import worker_process_init


def _get_broker_url() -> str:
    return os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")


def _get_backend_url() -> str:
    return os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")


# Redis BRPOP checks queues in listed order, so workers must subscribe with
# generation before embedding to prefer chat answers over reindex work.
QUEUE_GENERATION = "generation"
QUEUE_EMBEDDING = "embedding"
QUEUE_DEFAULT = "celery"
WORKER_QUEUES = f"{QUEUE_GENERATION},{QUEUE_DEFAULT},{QUEUE_EMBEDDING}"

celery_app = Celery("chatui", broker=_get_broker_url(), backend=_get_backend_url())
celery_app.conf.update(
    task_track_started=True,
    # High ceiling for long embedding/reindex jobs; chat generation overrides down.
    task_time_limit=60 * 60 * 12 + 300,
    task_soft_time_limit=60 * 60 * 12,
    worker_prefetch_multiplier=1,
    task_default_queue=QUEUE_DEFAULT,
    task_routes={
        "chatui.generate_chat_response": {"queue": QUEUE_GENERATION},
        "chatui.reindex_agent_source": {"queue": QUEUE_EMBEDDING},
        "chatui.index_space_chat": {"queue": QUEUE_EMBEDDING},
    },
    beat_schedule={
        "cleanup-incognito-chats": {
            "task": "chatui.cleanup_incognito_chats",
            "schedule": 60 * 5,
        },
        "cleanup-stale-generation-tasks": {
            "task": "chatui.cleanup_stale_generation_tasks",
            "schedule": 60 * 5,
        },
        "cleanup-retained-data-daily": {
            "task": "chatui.cleanup_retained_data",
            "schedule": 60 * 60 * 24,
        },
    },
)

celery_app.autodiscover_tasks(["app.workers"])


@worker_process_init.connect
def _warm_mcp_cache(**_kwargs) -> None:
    from app.services.mcp import refresh_mcp_cache

    try:
        asyncio.run(refresh_mcp_cache(force=True))
    except Exception:
        logging.getLogger(__name__).exception("Failed to warm MCP tool cache in worker")
