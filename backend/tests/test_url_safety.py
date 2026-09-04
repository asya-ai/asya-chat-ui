from __future__ import annotations

from app.core.url_safety import (
    is_blocked_hostname,
    is_blocked_http_url,
    is_literal_blocked_hostname,
)


def test_blocks_docker_and_internal_hostnames() -> None:
    assert is_literal_blocked_hostname("host.docker.internal")
    assert is_literal_blocked_hostname("gateway.docker.internal")
    assert is_literal_blocked_hostname("kubernetes.docker.internal")
    assert is_literal_blocked_hostname("metadata.google.internal")
    assert is_literal_blocked_hostname("something.internal")
    assert is_literal_blocked_hostname("printer.local")
    assert is_literal_blocked_hostname("app.localhost")
    assert is_literal_blocked_hostname("intranet.lan")


def test_blocks_loopback_and_private_ips() -> None:
    assert is_literal_blocked_hostname("localhost")
    assert is_literal_blocked_hostname("127.0.0.1")
    assert is_literal_blocked_hostname("0.0.0.0")
    assert is_literal_blocked_hostname("::1")
    assert is_literal_blocked_hostname("10.0.0.5")
    assert is_literal_blocked_hostname("192.168.1.1")
    assert is_literal_blocked_hostname("172.16.5.9")
    assert is_literal_blocked_hostname("169.254.169.254")
    assert is_literal_blocked_hostname("100.64.0.1")


def test_blocks_single_label_and_blank() -> None:
    assert is_literal_blocked_hostname("backend")
    assert is_literal_blocked_hostname("postgres")
    assert is_literal_blocked_hostname("")
    assert is_literal_blocked_hostname(None)
    assert is_literal_blocked_hostname("host.docker.internal.")


def test_literal_allows_public_hostnames_without_dns() -> None:
    # Must not DNS-resolve — scrape gating depends on this staying offline-safe.
    assert not is_literal_blocked_hostname("example.com")
    assert not is_literal_blocked_hostname("www.wikipedia.org")
    assert not is_literal_blocked_hostname("carflowsystem.pl")
    assert not is_literal_blocked_hostname("eng.lsm.lv")


def test_blocked_http_url_helpers() -> None:
    assert not is_blocked_http_url("https://example.com/path", resolve_dns=False)
    assert is_blocked_http_url("http://host.docker.internal:8080/", resolve_dns=False)
    assert is_blocked_http_url("ftp://example.com/file", resolve_dns=False)
    assert is_blocked_http_url("https://127.0.0.1/admin", resolve_dns=False)
    assert is_blocked_hostname("host.docker.internal", resolve_dns=False)
    assert not is_blocked_hostname("example.com", resolve_dns=False)
