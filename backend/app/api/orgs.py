from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.models import (
    ChatModel,
    Org,
    OrgMembership,
    OrgModel,
    OrgProviderConfig,
    Role,
    User,
)
from app.services.org_service import (
    ensure_default_roles,
    require_org_admin,
    require_super_admin,
)

router = APIRouter(prefix="/orgs", tags=["orgs"])


class OrgCreateRequest(BaseModel):
    name: str


class OrgRead(BaseModel):
    id: str
    name: str
    is_active: bool
    is_frozen: bool


class OrgMemberRead(BaseModel):
    user_id: str
    email: str
    role: str
    is_super_admin: bool


class OrgMemberUpdateRequest(BaseModel):
    role: str


class OrgUpdateRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    is_frozen: bool | None = None


class OrgWebSettingsRead(BaseModel):
    web_tools_enabled: bool
    web_search_enabled: bool
    web_scrape_enabled: bool
    web_grounding_openai: bool
    web_grounding_gemini: bool
    exec_network_enabled: bool
    exec_policy: str


class OrgWebSettingsUpdate(BaseModel):
    web_tools_enabled: bool | None = None
    web_search_enabled: bool | None = None
    web_scrape_enabled: bool | None = None
    web_grounding_openai: bool | None = None
    web_grounding_gemini: bool | None = None
    exec_network_enabled: bool | None = None
    exec_policy: str | None = None


class ProviderConfigRead(BaseModel):
    provider: str
    is_enabled: bool
    api_key_override_set: bool
    base_url_override: str | None
    endpoint_override: str | None


class ProviderConfigUpdate(BaseModel):
    provider: str
    is_enabled: bool | None = None
    api_key_override: str | None = None
    base_url_override: str | None = None
    endpoint_override: str | None = None


PROVIDERS = ["openai", "azure", "gemini", "groq", "anthropic"]


@router.post("", response_model=OrgRead)
def create_org(
    payload: OrgCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrgRead:
    if not current_user.is_super_admin:
        existing_membership = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == current_user.id)
        ).first()
        if existing_membership:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already belongs to an organization",
            )
    existing = session.exec(select(Org).where(Org.name == payload.name)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Org name already exists"
        )

    org = Org(name=payload.name)
    session.add(org)
    session.commit()
    session.refresh(org)

    admin_role, _ = ensure_default_roles(session, org.id)
    if not current_user.is_super_admin:
        membership = OrgMembership(
            org_id=org.id, user_id=current_user.id, role_id=admin_role.id
        )
        session.add(membership)
        session.commit()
    else:
        existing_membership = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == current_user.id)
        ).first()
        if not existing_membership:
            membership = OrgMembership(
                org_id=org.id, user_id=current_user.id, role_id=admin_role.id
            )
            session.add(membership)
            session.commit()

    return OrgRead(
        id=str(org.id),
        name=org.name,
        is_active=org.is_active,
        is_frozen=org.is_frozen,
    )


@router.get("", response_model=list[OrgRead])
def list_orgs(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OrgRead]:
    if current_user.is_super_admin:
        orgs = session.exec(select(Org)).all()
        return [
            OrgRead(
                id=str(org.id),
                name=org.name,
                is_active=org.is_active,
                is_frozen=org.is_frozen,
            )
            for org in orgs
        ]

    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == current_user.id)
    ).first()
    if not membership:
        return []
    org = session.exec(
        select(Org).where(Org.id == membership.org_id, Org.is_active == True)
    ).first()
    if not org:
        return []
    return [
        OrgRead(
            id=str(org.id),
            name=org.name,
            is_active=org.is_active,
            is_frozen=org.is_frozen,
        )
    ]


@router.get("/mine", response_model=list[OrgRead])
def list_my_orgs(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OrgRead]:
    orgs = session.exec(
        select(Org)
        .join(OrgMembership, OrgMembership.org_id == Org.id)
        .where(OrgMembership.user_id == current_user.id, Org.is_active == True)
    ).all()
    return [
        OrgRead(
            id=str(org.id),
            name=org.name,
            is_active=org.is_active,
            is_frozen=org.is_frozen,
        )
        for org in orgs
    ]


@router.get("/{org_id}/web-settings", response_model=OrgWebSettingsRead)
def get_web_settings(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrgWebSettingsRead:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc
    if not current_user.is_super_admin:
        require_org_admin(session, org_uuid, current_user.id)
    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    return OrgWebSettingsRead(
        web_tools_enabled=org.web_tools_enabled,
        web_search_enabled=org.web_search_enabled,
        web_scrape_enabled=org.web_scrape_enabled,
        web_grounding_openai=org.web_grounding_openai,
        web_grounding_gemini=org.web_grounding_gemini,
        exec_network_enabled=org.exec_network_enabled,
        exec_policy=org.exec_policy,
    )


@router.put("/{org_id}/web-settings", response_model=OrgWebSettingsRead)
def update_web_settings(
    org_id: str,
    payload: OrgWebSettingsUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrgWebSettingsRead:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc
    if not current_user.is_super_admin:
        require_org_admin(session, org_uuid, current_user.id)
    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    updates = payload.model_dump(exclude_unset=True)
    exec_policy = updates.get("exec_policy")
    if exec_policy is not None and exec_policy not in {"off", "prompt", "always"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid exec policy",
        )
    for key, value in updates.items():
        setattr(org, key, value)
    session.add(org)
    session.commit()
    session.refresh(org)
    return OrgWebSettingsRead(
        web_tools_enabled=org.web_tools_enabled,
        web_search_enabled=org.web_search_enabled,
        web_scrape_enabled=org.web_scrape_enabled,
        web_grounding_openai=org.web_grounding_openai,
        web_grounding_gemini=org.web_grounding_gemini,
        exec_network_enabled=org.exec_network_enabled,
        exec_policy=org.exec_policy,
    )


@router.get("/{org_id}/members", response_model=list[OrgMemberRead])
def list_members(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OrgMemberRead]:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    if not current_user.is_super_admin:
        require_org_admin(session, org_uuid, current_user.id)

    memberships = session.exec(
        select(OrgMembership).where(OrgMembership.org_id == org_uuid)
    ).all()
    members: list[OrgMemberRead] = []
    for membership in memberships:
        user = session.exec(select(User).where(User.id == membership.user_id)).first()
        role = session.exec(select(Role).where(Role.id == membership.role_id)).first()
        if not user or not role:
            continue
        members.append(
            OrgMemberRead(
                user_id=str(user.id),
                email=user.email,
                role=role.name,
                is_super_admin=user.is_super_admin,
            )
        )
    return members


@router.patch("/{org_id}/members/{user_id}", response_model=OrgMemberRead)
def update_member(
    org_id: str,
    user_id: str,
    payload: OrgMemberUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrgMemberRead:
    try:
        org_uuid = UUID(org_id)
        user_uuid = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id"
        ) from exc

    if not current_user.is_super_admin:
        require_org_admin(session, org_uuid, current_user.id)
        if current_user.id == user_uuid and payload.role == "member":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote yourself to member",
            )

    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_uuid, OrgMembership.user_id == user_uuid
        )
    ).first()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    ensure_default_roles(session, org_uuid)
    role = session.exec(
        select(Role).where(Role.org_id == org_uuid, Role.name == payload.role)
    ).first()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    membership.role_id = role.id
    session.add(membership)
    session.commit()
    user = session.exec(select(User).where(User.id == membership.user_id)).first()
    return OrgMemberRead(
        user_id=str(membership.user_id),
        email=user.email if user else "",
        role=role.name,
        is_super_admin=user.is_super_admin if user else False,
    )


@router.patch("/{org_id}", response_model=OrgRead)
def update_org(
    org_id: str,
    payload: OrgUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrgRead:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc
    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    if current_user.is_super_admin:
        if payload.is_active is not None:
            org.is_active = payload.is_active
        if payload.is_frozen is not None:
            org.is_frozen = payload.is_frozen
    else:
        require_org_admin(session, org_uuid, current_user.id)
        if payload.is_active is not None or payload.is_frozen is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super admins can change org status",
            )

    if payload.name is not None:
        org.name = payload.name.strip() or org.name

    session.add(org)
    session.commit()
    session.refresh(org)
    return OrgRead(
        id=str(org.id),
        name=org.name,
        is_active=org.is_active,
        is_frozen=org.is_frozen,
    )


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    require_super_admin(current_user)
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc
    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    org.is_active = False
    org.is_frozen = True
    session.add(org)
    session.commit()


def _ensure_org_admin_or_super(
    session: Session, org_id: UUID, current_user: User
) -> None:
    if current_user.is_super_admin:
        return
    require_org_admin(session, org_id, current_user.id)


@router.get("/{org_id}/providers", response_model=list[ProviderConfigRead])
def list_provider_configs(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProviderConfigRead]:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    _ensure_org_admin_or_super(session, org_uuid, current_user)

    configs = session.exec(
        select(OrgProviderConfig).where(OrgProviderConfig.org_id == org_uuid)
    ).all()
    by_provider = {config.provider: config for config in configs}
    results: list[ProviderConfigRead] = []
    for provider in PROVIDERS:
        config = by_provider.get(provider)
        results.append(
            ProviderConfigRead(
                provider=provider,
                is_enabled=config.is_enabled if config else True,
                api_key_override_set=bool(config and config.api_key_override),
                base_url_override=config.base_url_override if config else None,
                endpoint_override=config.endpoint_override if config else None,
            )
        )
    return results


@router.put("/{org_id}/providers", response_model=list[ProviderConfigRead])
def update_provider_configs(
    org_id: str,
    payload: list[ProviderConfigUpdate],
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProviderConfigRead]:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    _ensure_org_admin_or_super(session, org_uuid, current_user)

    for item in payload:
        if item.provider not in PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported provider",
            )
        config = session.exec(
            select(OrgProviderConfig).where(
                OrgProviderConfig.org_id == org_uuid,
                OrgProviderConfig.provider == item.provider,
            )
        ).first()
        if not config:
            config = OrgProviderConfig(
                org_id=org_uuid,
                provider=item.provider,
                is_enabled=True,
            )
        if item.is_enabled is not None:
            config.is_enabled = item.is_enabled
        if item.api_key_override is not None:
            config.api_key_override = item.api_key_override.strip() or None
        if item.base_url_override is not None:
            config.base_url_override = item.base_url_override.strip() or None
        if item.endpoint_override is not None:
            config.endpoint_override = item.endpoint_override.strip() or None
        session.add(config)

    session.commit()
    return list_provider_configs(org_id, session, current_user)
