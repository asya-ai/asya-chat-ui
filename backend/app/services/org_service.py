import re

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.secret_crypto import decrypt_secret
from app.models import Org, OrgMembership, OrgProviderConfig, Role

_LOGIN_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$"
)
_MAX_LOGIN_DOMAINS = 20


def normalize_login_domain(value: str) -> str | None:
    raw = value.strip().lower()
    if not raw:
        return None
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    if raw.startswith("["):
        end = raw.find("]")
        if end != -1:
            host = raw[1:end]
            return host or None
    if ":" in raw:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            raw = host
    raw = raw.rstrip(".")
    return raw or None


def is_valid_login_domain(domain: str) -> bool:
    if domain == "localhost":
        return True
    return bool(_LOGIN_DOMAIN_RE.match(domain))


def normalize_login_domains(domains: list[str] | str | None) -> list[str]:
    if not domains:
        return []
    if isinstance(domains, str):
        domains = [domains]
    seen: set[str] = set()
    normalized: list[str] = []
    for item in domains:
        if not isinstance(item, str):
            continue
        value = normalize_login_domain(item)
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def validate_login_domains(domains: list[str] | None) -> list[str]:
    normalized = normalize_login_domains(domains)
    for domain in normalized:
        if not is_valid_login_domain(domain):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid login domain: {domain}",
            )
    if len(normalized) > _MAX_LOGIN_DOMAINS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many login domains",
        )
    return normalized


def get_org_by_login_domain(session: Session, domain: str) -> Org | None:
    normalized = normalize_login_domain(domain)
    if not normalized:
        return None
    orgs = session.exec(
        select(Org).where(Org.is_active == True, Org.login_domains.is_not(None))
    ).all()
    for org in orgs:
        if normalized in normalize_login_domains(org.login_domains):
            return org
    return None


def ensure_login_domains_unique(
    session: Session, org_id, domains: list[str]
) -> None:
    for domain in domains:
        existing = get_org_by_login_domain(session, domain)
        if existing and existing.id != org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Login domain already in use: {domain}",
            )


def ensure_default_roles(session: Session, org_id) -> tuple[Role, Role]:
    admin_role = session.exec(
        select(Role).where(Role.org_id == org_id, Role.name == "admin")
    ).first()
    member_role = session.exec(
        select(Role).where(Role.org_id == org_id, Role.name == "member")
    ).first()

    if not admin_role:
        admin_role = Role(org_id=org_id, name="admin", is_default=False)
        session.add(admin_role)
    if not member_role:
        member_role = Role(org_id=org_id, name="member", is_default=True)
        session.add(member_role)

    session.commit()
    session.refresh(admin_role)
    session.refresh(member_role)
    return admin_role, member_role


def get_membership(session: Session, org_id, user_id) -> OrgMembership | None:
    return session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id, OrgMembership.user_id == user_id
        )
    ).first()


def require_org_admin(session: Session, org_id, user_id) -> OrgMembership:
    membership = get_membership(session, org_id, user_id)
    if not membership or not membership.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    if membership.role.name not in {"admin", "owner"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return membership


def require_org_member(
    session: Session, org_id, user_id, *, is_super_admin: bool = False
) -> OrgMembership | None:
    if is_super_admin:
        return None
    org = session.exec(select(Org).where(Org.id == org_id)).first()
    if not org or not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization inactive",
        )
    if org.is_frozen:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is frozen",
        )
    membership = get_membership(session, org_id, user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org membership required",
        )
    return membership


def require_super_admin(user) -> None:
    if not getattr(user, "is_super_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )




def get_provider_config(
    session: Session, org_id, provider: str
) -> OrgProviderConfig | None:
    return session.exec(
        select(OrgProviderConfig).where(
            OrgProviderConfig.org_id == org_id, OrgProviderConfig.provider == provider
        )
    ).first()


def require_provider_enabled(
    session: Session, org_id, provider: str
) -> OrgProviderConfig | None:
    config = get_provider_config(session, org_id, provider)
    if config and not config.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider is not enabled for this organization",
        )
    if config and config.api_key_override:
        config.api_key_override = decrypt_secret(config.api_key_override)
    return config
