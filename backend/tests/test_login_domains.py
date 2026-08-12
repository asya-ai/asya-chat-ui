from fastapi import HTTPException, Request

from app.api.auth import _build_invite_frontend_base
from app.models import Org
from app.services.org_service import (
    is_valid_login_domain,
    normalize_login_domain,
    normalize_login_domains,
    validate_login_domains,
)


def _request(*, origin: str | None = None, host: str = "app.example.com") -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "headers": headers})


def test_normalize_login_domain_strips_scheme_port_and_path():
    assert normalize_login_domain("HTTPS://Acme.Example.COM:443/app") == "acme.example.com"


def test_normalize_login_domain_handles_ipv6_host():
    assert normalize_login_domain("[::1]:8080") == "::1"


def test_normalize_login_domains_dedupes_and_lowercases():
    assert normalize_login_domains(["Acme.COM", "acme.com", " login.acme.com "]) == [
        "acme.com",
        "login.acme.com",
    ]


def test_normalize_login_domains_accepts_single_string():
    assert normalize_login_domains("Acme.Example.COM") == ["acme.example.com"]


def test_validate_login_domains_rejects_invalid_domain():
    try:
        validate_login_domains(["not a domain!"])
        raise AssertionError("expected validation error")
    except HTTPException as exc:
        assert exc.detail == "Invalid login domain: not a domain!"


def test_is_valid_login_domain_allows_localhost():
    assert is_valid_login_domain("localhost") is True
    assert is_valid_login_domain("acme.example.com") is True
    assert is_valid_login_domain("-bad.com") is False


def test_invite_frontend_base_uses_org_login_domain():
    org = Org(name="Acme", slug="acme", login_domains=["login.acme.com", "acme.example.com"])
    base = _build_invite_frontend_base(
        _request(origin="https://app.example.com"),
        org,
    )
    assert base == "https://login.acme.com"


def test_invite_frontend_base_falls_back_without_login_domain():
    org = Org(name="Acme", slug="acme", login_domains=None)
    base = _build_invite_frontend_base(
        _request(origin="https://app.example.com"),
        org,
    )
    assert base == "https://app.example.com"
