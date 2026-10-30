from __future__ import annotations

import logging

from celery.signals import worker_ready

logger = logging.getLogger(__name__)


@worker_ready.connect
def _requeue_interrupted_generations(**_kwargs: object) -> None:
    """Resume gens orphaned by a worker SIGKILL (compose rebuild past grace)."""
    from app.services.tools.exec_session import purge_leaked_exec_containers
    from app.workers.tasks import requeue_interrupted_generation_tasks

    try:
        purge_leaked_exec_containers()
    except Exception:
        logger.debug("Exec container purge on worker_ready failed", exc_info=True)

    try:
        result = requeue_interrupted_generation_tasks()
    except Exception:
        logger.exception("Failed to requeue interrupted generation tasks on boot")
        return
    requeued = int(result.get("requeued") or 0)
    if requeued:
        logger.info("worker_ready requeued interrupted generations count=%s", requeued)
