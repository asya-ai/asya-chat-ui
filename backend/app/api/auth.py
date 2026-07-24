from datetime import datetime, timedelta, timezone
import logging
import secrets
import re
import time
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from jose import JWTError, jwt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.core.config import get_super_admin_emails, settings
from app.core.secret_crypto import decrypt_secret
from app.core.security import (
    create_access_token,
    get_password_hash,
    validate_password,
    verify_password,
)
from app.models import Invite, Org, OrgMembership, PasswordReset, Role, User, UserMemory
from app.services.email_service import send_invite_email, send_password_reset_email
from app.services.org_service import (
    ensure_default_roles,
    get_org_by_login_domain,
    normalize_login_domain,
    require_org_admin,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

OIDC_CACHE: dict[str, dict[str, Any]] = {}
OIDC_JWKS: dict[str, dict[str, Any]] = {}
OIDC_CACHE_EXPIRES_AT: dict[str, float] = {}
OIDC_JWKS_EXPIRES_AT: dict[str, float] = {}
OIDC_CONFIG_TTL_SECONDS = 3600
OIDC_JWKS_TTL_SECONDS = 86400
OIDC_CACHE_MAX_SIZE = 64


def _cache_get(
    store: dict[str, dict[str, Any]],
    expires_store: dict[str, float],
    key: str,
) -> dict[str, Any] | None:
    expires_at = expires_store.get(key)
    if expires_at is None:
        return None
    now = time.time()
    if expires_at <= now:
        store.pop(key, None)
        expires_store.pop(key, None)
        return None
    return store.get(key)


def _cache_set(
    store: dict[str, dict[str, Any]],
    expires_store: dict[str, float],
    key: str,
    value: dict[str, Any],
    ttl_seconds: int,
) -> None:
    if len(store) >= OIDC_CACHE_MAX_SIZE and key not in store:
        oldest_key = min(expires_store.items(), key=lambda item: item[1])[0]
        store.pop(oldest_key, None)
        expires_store.pop(oldest_key, None)
    store[key] = value
    expires_store[key] = time.time() + ttl_seconds


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    identifier: str
    password: str
    org: str | None = None
    host: str | None = None


class LoginResolveRequest(BaseModel):
    identifier: str | None = None
    org: str | None = None
    host: str | None = None


class LoginResolveResponse(BaseModel):
    action: str
    redirect_url: str | None = None
    org: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class InviteCreateRequest(BaseModel):
    org_id: str
    email: EmailStr


class InviteAcceptRequest(BaseModel):
    token: str
    password: str | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


ALLOWED_LOCALES = {"en", "lv", "ja"}


class MeResponse(BaseModel):
    id: str
    email: EmailStr
    is_super_admin: bool
    is_admin: bool
    memory_enabled: bool
    locale: str | None = None


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


class InvitePreview(BaseModel):
    email: EmailStr
    org_name: str | None
    expires_at: datetime


def _normalize_identifier(value: str) -> str:
    return value.strip().lower()


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _is_admin_user(session: Session, user: User) -> bool:
    if user.is_super_admin:
        return True
    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).first()
    if membership:
        role = session.exec(select(Role).where(Role.id == membership.role_id)).first()
        if role and role.name in {"admin", "owner"}:
            return True
    return False


def _me_response(session: Session, user: User) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        email=user.email,
        is_super_admin=user.is_super_admin,
        is_admin=_is_admin_user(session, user),
        memory_enabled=user.memory_enabled,
        locale=user.locale,
    )


def _get_user_by_identifier(session: Session, identifier: str) -> User | None:
    normalized = _normalize_identifier(identifier)
    return session.exec(
        select(User).where(
            (func.lower(User.email) == normalized) | (func.lower(User.username) == normalized)
        )
    ).first()


def _get_org_by_slug(session: Session, slug_or_name: str) -> Org | None:
    normalized = slug_or_name.strip().lower()
    return session.exec(
        select(Org).where(
            Org.is_active == True,
            (Org.slug == normalized) | (Org.name.ilike(normalized)),
        )
    ).first()


def _get_request_login_host(request: Request, client_host: str | None = None) -> str | None:
    if client_host:
        return normalize_login_domain(client_host)
    forwarded = request.headers.get("x-forwarded-host")
    if forwarded:
        return normalize_login_domain(forwarded.split(",", 1)[0].strip())
    host = request.headers.get("host")
    if host:
        return normalize_login_domain(host)
    return None


def _resolve_login_org(
    session: Session,
    request: Request,
    *,
    explicit_org: str | None,
    client_host: str | None,
) -> Org | None:
    host = _get_request_login_host(request, client_host)
    if host:
        host_org = get_org_by_login_domain(session, host)
        if host_org:
            return host_org
    if explicit_org:
        explicit_org_match = _get_org_by_slug(session, explicit_org.strip())
        if explicit_org_match:
            return explicit_org_match
    active_orgs = session.exec(select(Org).where(Org.is_active == True).limit(2)).all()
    return active_orgs[0] if len(active_orgs) == 1 else None


def _org_login_slug(org: Org) -> str:
    return (org.slug or org.name).strip().lower()


def _get_membership_orgs(session: Session, user_id: UUID) -> list[Org]:
    return session.exec(
        select(Org)
        .join(OrgMembership, OrgMembership.org_id == Org.id)
        .where(OrgMembership.user_id == user_id, Org.is_active == True)
    ).all()


def _build_frontend_base(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme
        base = f"{scheme}://{forwarded_host}"
        forwarded_prefix = request.headers.get("x-forwarded-prefix")
        if forwarded_prefix:
            base = base.rstrip("/") + "/" + forwarded_prefix.strip("/")
        return base.rstrip("/")
    base = str(request.base_url).rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base.rstrip("/")


def _suggest_username(session: Session, email: str) -> str | None:
    base = email.split("@", 1)[0].strip().lower()
    if not base:
        return None
    candidate = re.sub(r"[^a-z0-9._-]+", "", base)
    if not candidate:
        return None
    existing = session.exec(select(User).where(User.username == candidate)).first()
    if not existing:
        return candidate
    suffix = 1
    while suffix < 1000:
        next_candidate = f"{candidate}{suffix}"
        existing = session.exec(
            select(User).where(User.username == next_candidate)
        ).first()
        if not existing:
            return next_candidate
        suffix += 1
    return None


def _slugify_org_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "org"


def _ensure_unique_org_slug(session: Session, base: str) -> str:
    slug = base
    suffix = 1
    while True:
        existing = session.exec(select(Org).where(Org.slug == slug)).first()
        if not existing:
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def _normalize_oidc_config_url(value: str) -> str:
    trimmed = value.strip()
    if "/.well-known/openid-configuration" in trimmed:
        return trimmed
    return trimmed.rstrip("/") + "/.well-known/openid-configuration"


async def _get_oidc_config(issuer_or_config: str) -> dict[str, Any]:
    config_url = _normalize_oidc_config_url(issuer_or_config)
    cached = _cache_get(OIDC_CACHE, OIDC_CACHE_EXPIRES_AT, config_url)
    if cached is not None:
        return cached
    url = config_url
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        config = response.json()
    _cache_set(
        OIDC_CACHE,
        OIDC_CACHE_EXPIRES_AT,
        config_url,
        config,
        OIDC_CONFIG_TTL_SECONDS,
    )
    return config


async def _get_oidc_jwks(jwks_uri: str) -> dict[str, Any]:
    cached = _cache_get(OIDC_JWKS, OIDC_JWKS_EXPIRES_AT, jwks_uri)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(jwks_uri)
        response.raise_for_status()
        jwks = response.json()
    _cache_set(
        OIDC_JWKS,
        OIDC_JWKS_EXPIRES_AT,
        jwks_uri,
        jwks,
        OIDC_JWKS_TTL_SECONDS,
    )
    return jwks


def _encode_oidc_state(*, org_id: UUID, nonce: str, redirect_base: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {
        "org_id": str(org_id),
        "nonce": nonce,
        "redirect_base": redirect_base,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode_oidc_state(state: str) -> dict[str, Any]:
    return jwt.decode(state, settings.secret_key, algorithms=[settings.jwt_algorithm])


async def _build_oidc_authorize_url(request: Request, org: Org) -> str:
    oidc_issuer = (org.oidc_issuer or "").strip()
    oidc_client_id = (org.oidc_client_id or "").strip()
    if not oidc_issuer or not oidc_client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SSO not configured")
    config = await _get_oidc_config(oidc_issuer)
    auth_endpoint = config.get("authorization_endpoint")
    if not auth_endpoint:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SSO not configured")
    nonce = secrets.token_urlsafe(16)
    redirect_base = _build_frontend_base(request)
    state = _encode_oidc_state(org_id=org.id, nonce=nonce, redirect_base=redirect_base)
    callback_url = _build_frontend_base(request).rstrip("/") + "/api/auth/oidc/callback"
    params = {
        "response_type": "code",
        "client_id": oidc_client_id,
        "redirect_uri": callback_url,
        "scope": org.oidc_scopes,
        "state": state,
        "nonce": nonce,
    }
    return auth_endpoint + "?" + urlencode(params)


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, session: Session = Depends(get_db)) -> TokenResponse:
    email = _normalize_email(payload.email)
    if not validate_password(payload.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 10 characters and include uppercase, lowercase, number, and special character.",
        )
    existing_user = session.exec(
        select(User).where(func.lower(User.email) == email)
    ).first()
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

    username = _suggest_username(session, email)
    user = User(
        email=email,
        username=username,
        hashed_password=get_password_hash(payload.password),
        is_super_admin=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    org_name = email.split("@", 1)[0] or "Default"
    slug = _ensure_unique_org_slug(session, _slugify_org_name(org_name))
    org = Org(name=org_name, slug=slug)
    session.add(org)
    session.commit()
    session.refresh(org)

    admin_role, _ = ensure_default_roles(session, org.id)
    membership = OrgMembership(
        org_id=org.id, user_id=user.id, role_id=admin_role.id
    )
    session.add(membership)
    session.commit()

    token = create_access_token(str(user.id), token_version=user.token_version)
    return TokenResponse(access_token=token)


@router.get("/registration-enabled", response_model=RegistrationStatusResponse)
def registration_enabled(session: Session = Depends(get_db)) -> RegistrationStatusResponse:
    any_user = session.exec(select(User)).first()
    return RegistrationStatusResponse(enabled=any_user is None)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, session: Session = Depends(get_db)
) -> TokenResponse:
    user = _get_user_by_identifier(session, payload.identifier)
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if user.auth_provider != "local":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSO required",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )
    org = _resolve_login_org(
        session,
        request,
        explicit_org=payload.org,
        client_host=payload.host,
    )
    if org:
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id, OrgMembership.org_id == org.id
            )
        ).first()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
    elif payload.org:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token = create_access_token(str(user.id), token_version=user.token_version)
    return TokenResponse(access_token=token)


@router.post("/login-resolve", response_model=LoginResolveResponse)
async def login_resolve(
    payload: LoginResolveRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> LoginResolveResponse:
    org = _resolve_login_org(
        session,
        request,
        explicit_org=payload.org,
        client_host=payload.host,
    )
    if not org and payload.org:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if org:
        org_slug = _org_login_slug(org)
        if not payload.identifier:
            if org.oidc_enabled:
                redirect_url = await _build_oidc_authorize_url(request, org)
                return LoginResolveResponse(
                    action="sso", redirect_url=redirect_url, org=org_slug
                )
            return LoginResolveResponse(action="local", org=org_slug)
        identifier = payload.identifier.strip()
        if not identifier:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid credentials")
        user = _get_user_by_identifier(session, identifier)
        if user:
            membership = session.exec(
                select(OrgMembership).where(
                    OrgMembership.user_id == user.id, OrgMembership.org_id == org.id
                )
            ).first()
            if membership and user.auth_provider == "local":
                return LoginResolveResponse(action="local", org=org_slug)
        if org.oidc_enabled:
            redirect_url = await _build_oidc_authorize_url(request, org)
            return LoginResolveResponse(
                action="sso", redirect_url=redirect_url, org=org_slug
            )
        return LoginResolveResponse(action="local", org=org_slug)

    if not payload.identifier:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Organization required")
    identifier = payload.identifier.strip()
    if not identifier:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid credentials")
    user = _get_user_by_identifier(session, identifier)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    orgs = _get_membership_orgs(session, user.id)
    if len(orgs) != 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Organization required")

    org = orgs[0]
    if org.oidc_enabled and user.auth_provider != "local":
        redirect_url = await _build_oidc_authorize_url(request, org)
        return LoginResolveResponse(action="sso", redirect_url=redirect_url)
    return LoginResolveResponse(action="local")


@router.get("/oidc/start")
async def oidc_start(
    org: str,
    request: Request,
    session: Session = Depends(get_db),
) -> RedirectResponse:
    org_record = _get_org_by_slug(session, org)
    if not org_record or not org_record.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SSO not configured")
    redirect_url = await _build_oidc_authorize_url(request, org_record)
    return RedirectResponse(redirect_url)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_description or error)
    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO response")
    try:
        state_payload = _decode_oidc_state(state)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO state") from exc

    org_id = state_payload.get("org_id")
    nonce = state_payload.get("nonce")
    redirect_base = state_payload.get("redirect_base") or _build_frontend_base(request)
    if not org_id or not nonce:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO state")

    org = session.exec(select(Org).where(Org.id == UUID(org_id))).first()
    if not org or not org.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SSO not configured")

    oidc_issuer = (org.oidc_issuer or "").strip()
    oidc_client_id = (org.oidc_client_id or "").strip()
    oidc_client_secret = (decrypt_secret(org.oidc_client_secret) or "").strip()
    config = await _get_oidc_config(oidc_issuer)
    config_issuer = (config.get("issuer") or oidc_issuer).strip()
    token_endpoint = config.get("token_endpoint")
    jwks_uri = config.get("jwks_uri")
    if not token_endpoint or not jwks_uri:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SSO not configured")

    callback_url = redirect_base.rstrip("/") + "/api/auth/oidc/callback"
    supported_methods = config.get("token_endpoint_auth_methods_supported") or []
    if isinstance(supported_methods, list):
        supported_methods = [str(method) for method in supported_methods]
    logger.info(
        "OIDC token exchange: token_endpoint=%s client_id=%s secret_len=%d "
        "redirect_uri=%s supported_methods=%s",
        token_endpoint,
        oidc_client_id,
        len(oidc_client_secret),
        callback_url,
        supported_methods,
    )
    async with httpx.AsyncClient(timeout=10) as client:
        token_payload = None
        last_exc: httpx.HTTPStatusError | None = None
        auth_methods = []
        if not supported_methods or "client_secret_basic" in supported_methods:
            if oidc_client_secret:
                auth_methods.append(
                    (
                        "basic",
                        {
                            "auth": (oidc_client_id, oidc_client_secret),
                            "data": {
                                "client_id": oidc_client_id,
                                "client_secret": oidc_client_secret,
                            },
                        },
                    )
                )
        if not supported_methods or "client_secret_post" in supported_methods:
            auth_methods.append(
                (
                    "post",
                    {
                        "data": {
                            "client_id": oidc_client_id,
                            "client_secret": oidc_client_secret,
                        }
                    },
                )
            )
        if not supported_methods or "none" in supported_methods:
            auth_methods.append(
                (
                    "none",
                    {
                        "data": {
                            "client_id": oidc_client_id,
                        }
                    },
                )
            )
        for label, extra in auth_methods:
            try:
                response = await client.post(
                    token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": callback_url,
                        **(extra.get("data") or {}),
                    },
                    **{k: v for k, v in extra.items() if k != "data"},
                )
                response.raise_for_status()
                token_payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.error(
                    "OIDC token exchange failed (%s): %s",
                    label,
                    exc.response.text,
                    exc_info=True,
                )
        if token_payload is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SSO token exchange failed",
            ) from last_exc

    id_token = token_payload.get("id_token")
    if not id_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO response")

    jwks = await _get_oidc_jwks(jwks_uri)
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    keys = jwks.get("keys", [])
    key = next((item for item in keys if item.get("kid") == kid), None) or (keys[0] if keys else None)
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO response")

    claims = jwt.decode(
        id_token,
        key,
        algorithms=[header.get("alg", "RS256")],
        audience=oidc_client_id,
        issuer=config_issuer,
    )
    if claims.get("nonce") != nonce:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO response")

    email_claim = org.oidc_email_claim or "email"
    username_claim = org.oidc_username_claim or "preferred_username"
    email = claims.get(email_claim)
    username = claims.get(username_claim)
    if isinstance(email, str):
        email = email.strip().lower()
    if isinstance(username, str):
        username = username.strip().lower()

    user = None
    if email:
        user = session.exec(select(User).where(func.lower(User.email) == email)).first()
    if not user and username:
        user = session.exec(select(User).where(User.username == username)).first()

    if user:
        membership = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == user.id)
        ).first()
        if membership and membership.org_id != org.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already belongs to another organization",
            )
    else:
        if not org.oidc_auto_create_users:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not allowed")
        if not email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing email claim")
        if not username:
            username = _suggest_username(session, email)
        user = User(
            email=email,
            username=username,
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            auth_provider="oidc",
            is_super_admin=email.lower() in get_super_admin_emails(),
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    org_membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id, OrgMembership.user_id == user.id
        )
    ).first()
    if not org_membership:
        _, member_role = ensure_default_roles(session, org.id)
        membership = OrgMembership(
            org_id=org.id, user_id=user.id, role_id=member_role.id
        )
        session.add(membership)
        session.commit()

    token = create_access_token(str(user.id), token_version=user.token_version)
    redirect_url = f"{redirect_base}/sso-callback?token={token}"
    return RedirectResponse(redirect_url)


@router.get("/me", response_model=MeResponse)
def get_me(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    return _me_response(session, current_user)


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
    if not validate_password(payload.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 10 characters and include uppercase, lowercase, number, and special character.",
        )
    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.token_version += 1
    existing_resets = session.exec(
        select(PasswordReset).where(
            PasswordReset.user_id == current_user.id,
            PasswordReset.used_at.is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for reset in existing_resets:
        reset.used_at = now
        session.add(reset)
    session.add(current_user)
    session.commit()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    current_user.token_version += 1
    session.add(current_user)
    session.commit()


class MemoryToggleRequest(BaseModel):
    memory_enabled: bool


@router.patch("/me/memory", response_model=MeResponse)
def toggle_memory(
    payload: MemoryToggleRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    current_user.memory_enabled = payload.memory_enabled
    session.add(current_user)
    session.commit()
    return _me_response(session, current_user)


class LocaleUpdateRequest(BaseModel):
    locale: str


@router.patch("/me/locale", response_model=MeResponse)
def update_locale(
    payload: LocaleUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    locale = payload.locale.strip().lower()
    if locale not in ALLOWED_LOCALES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid locale (use {'/'.join(sorted(ALLOWED_LOCALES))})",
        )
    current_user.locale = locale
    session.add(current_user)
    session.commit()
    return _me_response(session, current_user)


class MemoryRead(BaseModel):
    id: str
    content: str
    created_at: str


class MemoryUpdateRequest(BaseModel):
    content: str


@router.get("/me/memories", response_model=list[MemoryRead])
def list_memories(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MemoryRead]:
    rows = session.exec(
        select(UserMemory)
        .where(UserMemory.user_id == current_user.id)
        .order_by(UserMemory.created_at)
    ).all()
    return [
        MemoryRead(id=str(m.id), content=m.content, created_at=m.created_at.isoformat())
        for m in rows
    ]


@router.patch("/me/memories/{memory_id}", response_model=MemoryRead)
def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemoryRead:
    try:
        mid = UUID(memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id") from exc
    memory = session.get(UserMemory, mid)
    if not memory or memory.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    memory.content = payload.content.strip()
    session.add(memory)
    session.commit()
    return MemoryRead(
        id=str(memory.id), content=memory.content, created_at=memory.created_at.isoformat()
    )


@router.delete("/me/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        mid = UUID(memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id") from exc
    memory = session.get(UserMemory, mid)
    if not memory or memory.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    session.delete(memory)
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
    return _me_response(session, user)


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

    invite_email = _normalize_email(payload.email)
    token = secrets.token_urlsafe(32)
    invite = Invite(
        org_id=org.id,
        email=invite_email,
        token=token,
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.invite_expire_hours),
        created_by_user_id=current_user.id,
    )
    session.add(invite)
    session.commit()
    session.refresh(invite)
    org_hint = (org.slug or org.name or "").strip().lower()
    params = {"token": invite.token}
    if org_hint:
        params["org"] = org_hint
    invite_url = f"{request.base_url}invite?{urlencode(params)}"
    try:
        send_invite_email(
            to_email=invite.email,
            org_name=org.name,
            invite_url=invite_url,
        )
    except Exception as exc:
        logger.warning(
            "Invite email send failed email=%s org_id=%s error=%s",
            invite.email,
            invite.org_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send invite email",
        ) from exc
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


@router.get("/invites/preview", response_model=InvitePreview)
def preview_invite(token: str, session: Session = Depends(get_db)) -> InvitePreview:
    invite = session.exec(select(Invite).where(Invite.token == token)).first()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite used")
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite expired")
    org = session.exec(select(Org).where(Org.id == invite.org_id)).first()
    return InvitePreview(
        email=invite.email,
        org_name=org.name if org else None,
        expires_at=expires_at,
    )


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
    org = session.exec(select(Org).where(Org.id == invite.org_id)).first()
    org_hint = ((org.slug if org else None) or (org.name if org else None) or "").strip().lower()
    params = {"token": invite.token}
    if org_hint:
        params["org"] = org_hint
    invite_url = f"{request.base_url}invite?{urlencode(params)}"
    try:
        send_invite_email(
            to_email=invite.email,
            org_name=org.name if org else "your organization",
            invite_url=invite_url,
        )
    except Exception as exc:
        logger.warning(
            "Invite resend email failed email=%s org_id=%s error=%s",
            invite.email,
            invite.org_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send invite email",
        ) from exc
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

    email = invite.email.strip().lower()
    user = session.exec(select(User).where(func.lower(User.email) == email)).first()
    if not user:
        if not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password required for new user",
            )
        if not validate_password(payload.password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 10 characters and include uppercase, lowercase, number, and special character.",
            )
        username = _suggest_username(session, email)
        user = User(
            email=email,
            username=username,
            hashed_password=get_password_hash(payload.password),
            auth_provider="local",
            is_super_admin=email.lower() in get_super_admin_emails(),
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

    token = create_access_token(str(user.id), token_version=user.token_version)
    return TokenResponse(access_token=token)


@router.post("/password-reset", status_code=status.HTTP_204_NO_CONTENT)
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> None:
    email = _normalize_email(payload.email)
    user = session.exec(select(User).where(func.lower(User.email) == email)).first()
    if not user:
        return
    now = datetime.now(timezone.utc)
    existing_resets = session.exec(
        select(PasswordReset).where(
            PasswordReset.user_id == user.id,
            PasswordReset.used_at.is_(None),
        )
    ).all()
    for existing in existing_resets:
        existing.used_at = now
        session.add(existing)
    token = secrets.token_urlsafe(32)
    reset = PasswordReset(
        user_id=user.id,
        token=token,
        expires_at=now + timedelta(hours=settings.password_reset_expire_hours),
    )
    session.add(reset)
    session.commit()
    base = request.headers.get("origin") or str(request.base_url)
    if not base.endswith("/"):
        base += "/"
    reset_url = f"{base}reset-password?token={token}"
    try:
        send_password_reset_email(to_email=user.email, reset_url=reset_url)
    except Exception as exc:
        logger.warning("Password reset email failed email=%s error=%s", user.email, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset email",
        ) from exc


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(
    payload: PasswordResetConfirm,
    session: Session = Depends(get_db),
) -> None:
    if len(payload.token) > 256:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token",
        )
    reset = session.exec(
        select(PasswordReset).where(PasswordReset.token == payload.token)
    ).first()
    if not reset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    if reset.used_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Token already used")
    expires_at = reset.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Token expired")
    user = session.exec(select(User).where(User.id == reset.user_id)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not validate_password(payload.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 10 characters and include uppercase, lowercase, number, and special character.",
        )
    now = datetime.now(timezone.utc)
    user.hashed_password = get_password_hash(payload.new_password)
    user.token_version += 1
    reset.used_at = now
    other_resets = session.exec(
        select(PasswordReset).where(
            PasswordReset.user_id == user.id,
            PasswordReset.used_at.is_(None),
            PasswordReset.id != reset.id,
        )
    ).all()
    for other in other_resets:
        other.used_at = now
        session.add(other)
    session.add(user)
    session.add(reset)
    session.commit()
