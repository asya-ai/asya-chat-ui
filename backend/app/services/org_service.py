from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models import Org, OrgMembership, OrgProviderConfig, Role


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
    return config
