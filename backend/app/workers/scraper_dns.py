from __future__ import annotations

import logging
import os
import socket
from urllib.parse import urlparse

from celery.signals import worker_process_init

logger = logging.getLogger(__name__)


@worker_process_init.connect
def _log_scraper_dns(**_kwargs: object) -> None:
    """Surface SCRAPER_URL DNS problems at worker boot (not mid-scrape)."""
    raw = os.getenv("SCRAPER_URL", "http://scraper:3001")
    host = urlparse(raw).hostname or raw
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET)
        addrs = sorted({item[4][0] for item in infos})
        logger.info(
            "worker scraper DNS ok SCRAPER_URL=%s host=%s addrs=%s",
            raw,
            host,
            addrs,
        )
    except OSError as exc:
        logger.error(
            "worker scraper DNS FAILED SCRAPER_URL=%s host=%s err=%s "
            "(web_scrape will fail until Docker DNS can resolve this host)",
            raw,
            host,
            exc,
        )
