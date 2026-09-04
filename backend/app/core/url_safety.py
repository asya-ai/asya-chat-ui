"""SSRF guards for user-supplied URLs (scrapes, downloads, agent sources)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Docker / cloud special-use names that resolve inside private networks.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "host.docker.internal",
        "gateway.docker.internal",
        "kubernetes.docker.internal",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }
)

_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp")


def normalize_hostname(hostname: str | None) -> str:
    if not hostname:
        return ""
    # Strip trailing FQDN dots and normalize case.
    return hostname.strip().rstrip(".").lower()


def is_literal_blocked_hostname(hostname: str | None) -> bool:
    """
    Block private/special hosts without DNS.

    Used by web_scrape so the backend does not depend on resolving public
    sites (scraper performs DNS + non-global IP checks at fetch time).
    """
    host = normalize_hostname(hostname)
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES:
        return True
    if host.endswith(_BLOCKED_SUFFIXES):
        return True
    # Single-label names (docker compose services, intranet short names).
    if "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except ValueError:
        return False


def resolves_to_non_global(hostname: str | None) -> bool:
    """True when DNS answers include any non-global address (or DNS fails)."""
    host = normalize_hostname(hostname)
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Fail closed for direct backend fetches (downloads / agent URL import).
        return True
    if not infos:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if not ip.is_global:
            return True
    return False


def is_blocked_hostname(hostname: str | None, *, resolve_dns: bool = True) -> bool:
    """
    True when a hostname must not be fetched by tools (SSRF).

    resolve_dns=False: literal / suffix / IP-literal checks only (for scrape
    gating — scraper validates resolved IPs).
    resolve_dns=True: also reject hosts whose DNS answers are non-global
    (for backend-side httpx fetches).
    """
    if is_literal_blocked_hostname(hostname):
        return True
    if not resolve_dns:
        return False
    return resolves_to_non_global(hostname)


def is_blocked_http_url(url: str, *, resolve_dns: bool = True) -> bool:
    """True when a URL is unsafe to fetch (scheme or host)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    if parsed.scheme not in {"http", "https"}:
        return True
    return is_blocked_hostname(parsed.hostname, resolve_dns=resolve_dns)
