from __future__ import annotations

import os

from celery import Celery


def _get_broker_url() -> str:
    return os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")


def _get_backend_url() -> str:
    return os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")


celery_app = Celery("chatui", broker=_get_broker_url(), backend=_get_backend_url())
celery_app.conf.update(
    task_track_started=True,
    task_time_limit=60 * 20,
    task_soft_time_limit=60 * 15,
    worker_prefetch_multiplier=1,
)

celery_app.autodiscover_tasks(["app.workers"])
