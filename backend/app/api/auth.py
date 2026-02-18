from datetime import datetime, timedelta, timezone
import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.core.config import get_super_admin_emails, settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models import Invite, Org, OrgMembership, Role, User
from app.services.org_service import ensure_default_roles, require_org_admin

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class InviteCreateRequest(BaseModel):
    org_id: str
    email: EmailStr


class InviteAcceptRequest(BaseModel):
    token: str
    password: str | None = None


class MeResponse(BaseModel):
    id: str
    email: EmailStr
    is_super_admin: bool
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class SuperAdminUpdateRequest(BaseModel):
    is_super_admin: bool


class RegistrationStatusResponse(BaseModel):
    enabled: bool


class InviteRead(BaseModel):
    id: str
    org_id: str
    email: EmailStr
    token: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, session: Session = Depends(get_db)) -> TokenResponse:
    existing_user = session.exec(select(User).where(User.email == payload.email)).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    any_user = session.exec(select(User)).first()
    if any_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration disabled",
        )

    user = User(
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        is_super_admin=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    org_name = payload.email.split("@", 1)[0] or "Default"
    org = Org(name=org_name)
    session.add(org)
    session.commit()
    session.refresh(org)

    admin_role, _ = ensure_default_roles(session, org.id)
    membership = OrgMembership(
        org_id=org.id, user_id=user.id, role_id=admin_role.id
    )
    session.add(membership)
    session.commit()

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


@router.get("/registration-enabled", response_model=RegistrationStatusResponse)
def registration_enabled(session: Session = Depends(get_db)) -> RegistrationStatusResponse:
    any_user = session.exec(select(User)).first()
    return RegistrationStatusResponse(enabled=any_user is None)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_db)) -> TokenResponse:
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=MeResponse)
def get_me(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    is_admin = False
    if current_user.is_super_admin:
        is_admin = True
    else:
        membership = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == current_user.id)
        ).first()
        if membership:
            role = session.exec(select(Role).where(Role.id == membership.role_id)).first()
            if role and role.name in {"admin", "owner"}:
                is_admin = True

    return MeResponse(
        id=str(current_user.id),
        email=current_user.email,
        is_super_admin=current_user.is_super_admin,
        is_admin=is_admin,
    )


@router.patch("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    current_user.hashed_password = get_password_hash(payload.new_password)
    session.add(current_user)
    session.commit()


@router.patch("/users/{user_id}/super-admin", response_model=MeResponse)
def update_super_admin(
    user_id: str,
    payload: SuperAdminUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required"
        )
    try:
        user_uuid = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id"
        ) from exc
    if current_user.id == user_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own superadmin status",
        )
    user = session.exec(select(User).where(User.id == user_uuid)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_super_admin = payload.is_super_admin
    session.add(user)
    session.commit()
    session.refresh(user)
    return MeResponse(
        id=str(user.id),
        email=user.email,
        is_super_admin=user.is_super_admin,
        is_admin=user.is_super_admin,
    )


@router.post("/invites")
def create_invite(
    payload: InviteCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Invite:
    try:
        org_id = UUID(payload.org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    org = session.exec(select(Org).where(Org.id == org_id)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    if not current_user.is_super_admin:
        require_org_admin(session, org.id, current_user.id)

    token = secrets.token_urlsafe(32)
    invite = Invite(
        org_id=org.id,
        email=payload.email,
        token=token,
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.invite_expire_hours),
        created_by_user_id=current_user.id,
    )
    session.add(invite)
    session.commit()
    session.refresh(invite)
    logger.info("Invite created email=%s org_id=%s", invite.email, invite.org_id)
    return invite


@router.get("/invites", response_model=list[InviteRead])
def list_invites(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[InviteRead]:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    if not current_user.is_super_admin:
        require_org_admin(session, org.id, current_user.id)

    invites = session.exec(
        select(Invite).where(Invite.org_id == org_uuid, Invite.accepted_at == None)
    ).all()
    return [
        InviteRead(
            id=str(invite.id),
            org_id=str(invite.org_id),
            email=invite.email,
            token=invite.token,
            expires_at=invite.expires_at,
            accepted_at=invite.accepted_at,
            created_at=invite.created_at,
        )
        for invite in invites
    ]


@router.post("/invites/{invite_id}/resend", response_model=InviteRead)
def resend_invite(
    invite_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InviteRead:
    try:
        invite_uuid = UUID(invite_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid invite id"
        ) from exc
    invite = session.exec(select(Invite).where(Invite.id == invite_uuid)).first()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite used")
    if not current_user.is_super_admin:
        require_org_admin(session, invite.org_id, current_user.id)

    invite.token = secrets.token_urlsafe(32)
    invite.expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.invite_expire_hours
    )
    session.add(invite)
    session.commit()
    session.refresh(invite)
    logger.info("Invite resent email=%s org_id=%s", invite.email, invite.org_id)
    return InviteRead(
        id=str(invite.id),
        org_id=str(invite.org_id),
        email=invite.email,
        token=invite.token,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        created_at=invite.created_at,
    )


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_invite(
    invite_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        invite_uuid = UUID(invite_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid invite id"
        ) from exc
    invite = session.exec(select(Invite).where(Invite.id == invite_uuid)).first()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if not current_user.is_super_admin:
        require_org_admin(session, invite.org_id, current_user.id)
    session.delete(invite)
    session.commit()


@router.post("/invites/accept", response_model=TokenResponse)
def accept_invite(
    payload: InviteAcceptRequest, session: Session = Depends(get_db)
) -> TokenResponse:
    invite = session.exec(select(Invite).where(Invite.token == payload.token)).first()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite used")
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite expired")

    user = session.exec(select(User).where(User.email == invite.email)).first()
    if not user:
        if not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password required for new user",
            )
        user = User(
            email=invite.email,
            hashed_password=get_password_hash(payload.password),
            is_super_admin=invite.email.lower() in get_super_admin_emails(),
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    existing_membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).first()
    if existing_membership and existing_membership.org_id != invite.org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already belongs to another organization",
        )

    admin_role, member_role = ensure_default_roles(session, invite.org_id)

    org_membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == invite.org_id, OrgMembership.user_id == user.id
        )
    ).first()
    if not org_membership:
        membership = OrgMembership(
            org_id=invite.org_id, user_id=user.id, role_id=member_role.id
        )
        session.add(membership)

    invite.accepted_at = datetime.now(timezone.utc)
    session.add(invite)
    session.commit()

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)
