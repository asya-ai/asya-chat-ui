from fastapi import Request
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.auth import _org_selection_required, _resolve_login_org
from app.models import Org


def _request(host: str = "chat.example.com") -> Request:
    return Request({"type": "http", "headers": [(b"host", host.encode())]})


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=[Org.__table__])
    return Session(engine)


def test_resolves_the_only_active_org():
    with _session() as session:
        org = Org(name="Acme", slug="acme")
        session.add(org)
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request(),
            explicit_org=None,
            client_host=None,
        )

        assert resolved is not None
        assert resolved.id == org.id
        assert source == "single"


def test_ignores_inactive_orgs_when_resolving_the_only_active_org():
    with _session() as session:
        active_org = Org(name="Acme", slug="acme")
        session.add(active_org)
        session.add(Org(name="Old Acme", slug="old-acme", is_active=False))
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request(),
            explicit_org=None,
            client_host=None,
        )

        assert resolved is not None
        assert resolved.id == active_org.id
        assert source == "single"


def test_does_not_resolve_when_multiple_active_orgs_exist():
    with _session() as session:
        session.add(Org(name="Acme", slug="acme"))
        session.add(Org(name="Beta", slug="beta"))
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request(),
            explicit_org=None,
            client_host=None,
        )

        assert resolved is None
        assert source is None


def test_invalid_saved_org_falls_back_to_the_only_active_org():
    with _session() as session:
        org = Org(name="Acme", slug="acme")
        session.add(org)
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request(),
            explicit_org="missing",
            client_host=None,
        )

        assert resolved is not None
        assert resolved.id == org.id
        assert source == "single"


def test_valid_explicit_org_takes_precedence_with_multiple_active_orgs():
    with _session() as session:
        session.add(Org(name="Acme", slug="acme"))
        selected_org = Org(name="Beta", slug="beta")
        session.add(selected_org)
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request(),
            explicit_org="beta",
            client_host=None,
        )

        assert resolved is not None
        assert resolved.id == selected_org.id
        assert source == "explicit"


def test_login_domain_resolves_org_and_locks_selection():
    with _session() as session:
        domain_org = Org(name="Acme", slug="acme", login_domains=["chat.acme.com"])
        session.add(domain_org)
        session.add(Org(name="Beta", slug="beta"))
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request("api.internal:8000"),
            explicit_org=None,
            client_host="chat.acme.com:443",
        )

        assert resolved is not None
        assert resolved.id == domain_org.id
        assert source == "domain"


def test_login_domain_takes_precedence_over_explicit_org():
    with _session() as session:
        domain_org = Org(name="Acme", slug="acme", login_domains=["chat.acme.com"])
        other = Org(name="Beta", slug="beta")
        session.add(domain_org)
        session.add(other)
        session.commit()

        resolved, source = _resolve_login_org(
            session,
            _request(),
            explicit_org="beta",
            client_host="chat.acme.com",
        )

        assert resolved is not None
        assert resolved.id == domain_org.id
        assert source == "domain"


def test_org_selection_not_required_for_single_active_org():
    with _session() as session:
        session.add(Org(name="Acme", slug="acme"))
        session.add(Org(name="Old Acme", slug="old-acme", is_active=False))
        session.commit()

        assert _org_selection_required(session) is False


def test_org_selection_required_for_multiple_active_orgs():
    with _session() as session:
        session.add(Org(name="Acme", slug="acme"))
        session.add(Org(name="Beta", slug="beta"))
        session.commit()

        assert _org_selection_required(session) is True
