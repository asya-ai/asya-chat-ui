"""Embed ms-graph-mcp inside the ChatUI API process.

Config (client_id / tenant_id) comes from the Microsoft OAuth provider admin
row — not from GRAPH_MCP_* env vars. The ASGI app is rebuilt when those ids
change so an Integrations UI save takes effect without restarting the process.

Chat generation runs on Celery workers, which must reach this mount over the
Docker network (see GRAPH_MCP_EMBED_URL) — not via 127.0.0.1 on the worker.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Default when GRAPH_MCP_EMBED_URL is unset (local uvicorn + local worker).
# In Docker, compose sets GRAPH_MCP_EMBED_URL=http://backend:8000/... so Celery
# workers reach the API mount (they have no local listener on :8000).
EMBEDDED_GRAPH_MCP_URL = "http://127.0.0.1:8000/internal/graph-mcp/mcp"


def embedded_graph_mcp_url() -> str:
    from app.core.config import settings

    url = (settings.graph_mcp_embed_url or "").strip().rstrip("/")
    return url or EMBEDDED_GRAPH_MCP_URL


def is_embedded_graph_mcp_url(url: str | None) -> bool:
    """True when URL targets the ChatUI-embedded Graph MCP mount."""
    if not url:
        return False
    raw = url.strip().rstrip("/")
    if raw == embedded_graph_mcp_url():
        return True
    path = (urlparse(raw).path or "").rstrip("/")
    return path.endswith("/internal/graph-mcp/mcp")


def _allowed_hosts_for_embed(url: str) -> str:
    host = urlparse(url).hostname or "127.0.0.1"
    return ",".join(sorted({"127.0.0.1", "localhost", host}))


# Graph access tokens use either the resource URI or the well-known app id.
_GRAPH_AUDIENCES = (
    "https://graph.microsoft.com,"
    "00000003-0000-0000-c000-000000000000"
)
_MULTI_TENANT_IDS = frozenset({"common", "organizations", "consumers"})
# Well-known Entra tenant id for personal Microsoft accounts (MSA).
_CONSUMERS_TID = "9188040d-6c67-4c5b-b112-36a304b66dad"
_TEAMS_GRAPH_PATH_MARKERS = (
    "/chats",
    "/joinedTeams",
    "/teams/",
    "/teamwork",
)
_jwt_issuer_patch_installed = False
_graph_client_patch_installed = False


def _token_is_personal_msa(token: str) -> bool:
    if not token or token.count(".") != 2:
        return False
    try:
        import jwt

        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return False
    tid = str(payload.get("tid") or "").strip().lower()
    if tid == _CONSUMERS_TID:
        return True
    iss = str(payload.get("iss") or "").lower()
    return "consumers" in iss


def _is_teams_graph_path(path: str) -> bool:
    return any(marker in path for marker in _TEAMS_GRAPH_PATH_MARKERS)


def _issuers_for_tid(tid: str) -> set[str]:
    tid = tid.strip()
    if not tid:
        return set()
    return {
        f"https://login.microsoftonline.com/{tid}/v2.0",
        f"https://sts.windows.net/{tid}/",
        f"https://login.microsoftonline.com/{tid}/",
    }


def _token_debug_meta(token: str) -> str:
    """Safe token shape for logs (never the raw secret)."""
    if not token:
        return "empty"
    dots = token.count(".")
    if dots != 2:
        return f"non-jwt parts={dots + 1} len={len(token)}"
    try:
        import jwt

        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:
        return f"jwt-unreadable ({exc})"
    aud = payload.get("aud")
    return (
        f"aud={aud!r} iss={payload.get('iss')!r} tid={payload.get('tid')!r} "
        f"azp={(payload.get('azp') or payload.get('appid'))!r} "
        f"exp={payload.get('exp')!r} scp={str(payload.get('scp') or '')[:96]!r}"
    )


def _sanitize_graph_get_params(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Adjust OData/header kwargs before calling Graph (ms-graph-mcp quirks)."""
    out = dict(params)
    if "/todo/" in path:
        out.pop("$select", None)
    if "$search" in out:
        headers = dict(out.pop("headers", None) or {})
        headers.setdefault("ConsistencyLevel", "eventual")
        out["headers"] = headers
    return out


def _personal_msa_teams_http_error(path: str) -> Exception:
    """Graph Teams APIs are work/school only — fail with a clear 403 body."""
    import httpx

    url = f"https://graph.microsoft.com/v1.0{path}"
    request = httpx.Request("GET", url)
    response = httpx.Response(
        403,
        request=request,
        json={
            "error": {
                "code": "Forbidden",
                "message": (
                    "Microsoft Graph Teams APIs are not available for personal "
                    "Microsoft accounts. Use a work or school Microsoft 365 account."
                ),
            }
        },
    )
    return httpx.HTTPStatusError(
        "Teams Graph requires work/school account",
        request=request,
        response=response,
    )


def _install_graph_client_patches() -> None:
    """Work around Graph quirks in ms-graph-mcp tool HTTP calls.

    - Drop ``$select`` on ``/me/todo/...`` — Graph returns 400
      ``RequestBroker--ParseUri`` for personal (and often work) accounts.
    - Send ``ConsistencyLevel: eventual`` when ``$search`` is used.
    - Short-circuit Teams Graph calls for personal MSA with a clear error.
    """
    global _graph_client_patch_installed
    if _graph_client_patch_installed:
        return

    import ms_graph_mcp.client as client_mod
    import ms_graph_mcp.errors as errors_mod

    original_get = client_mod.graph_get
    original_post = getattr(client_mod, "graph_post", None)
    original_graph_error = errors_mod.graph_error_response

    def _graph_error_response(exc: Any, *, scope: str = "", tool: str = "") -> Any:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        detail = ""
        try:
            if response is not None:
                detail = str((response.json().get("error") or {}).get("message") or "")
        except Exception:
            detail = ""
        if status in (401, 403) and (
            "personal Microsoft" in detail
            or "work or school" in detail
            or "not available for personal" in detail
        ):
            return {
                "error": "UNSUPPORTED_ACCOUNT",
                "message": detail
                or (
                    "Microsoft Graph Teams APIs require a work or school "
                    "Microsoft 365 account. Personal Microsoft accounts are not supported."
                ),
                "retryable": False,
            }
        if status == 401 and (
            scope.startswith("Chat")
            or scope.startswith("Channel")
            or scope.startswith("Team")
        ):
            return {
                "error": "UNSUPPORTED_ACCOUNT",
                "message": (
                    f"Teams Graph call failed (HTTP 401; needs {scope or 'Teams'}). "
                    "Personal Microsoft accounts are not supported for Teams via Graph — "
                    "reconnect with a work or school Microsoft 365 account. "
                    "Do not retry with this account."
                ),
                "retryable": False,
                "scope": scope,
            }
        return original_graph_error(exc, scope=scope, tool=tool)

    async def _graph_get(access_token: str, path: str, **params: Any) -> dict:
        if _is_teams_graph_path(path) and _token_is_personal_msa(access_token):
            raise _personal_msa_teams_http_error(path)
        return await original_get(
            access_token, path, **_sanitize_graph_get_params(path, params)
        )

    async def _graph_post(
        access_token: str, path: str, body: Any = None, **params: Any
    ) -> dict:
        if _is_teams_graph_path(path) and _token_is_personal_msa(access_token):
            raise _personal_msa_teams_http_error(path)
        assert original_post is not None
        return await original_post(access_token, path, body, **params)

    client_mod.graph_get = _graph_get  # type: ignore[method-assign]
    if original_post is not None:
        client_mod.graph_post = _graph_post  # type: ignore[method-assign]
    errors_mod.graph_error_response = _graph_error_response  # type: ignore[method-assign]

    # Tools bind helpers at import time — rebind on those modules too.
    for mod_name in (
        "ms_graph_mcp.tasks",
        "ms_graph_mcp.tasks_write",
        "ms_graph_mcp.people",
        "ms_graph_mcp.contacts",
        "ms_graph_mcp.chats",
        "ms_graph_mcp.teams",
        "ms_graph_mcp.email",
        "ms_graph_mcp.calendar",
        "ms_graph_mcp.calendar_write",
        "ms_graph_mcp.files",
        "ms_graph_mcp.files_write",
        "ms_graph_mcp.directory",
        "ms_graph_mcp.search",
        "ms_graph_mcp.meetings",
        "ms_graph_mcp.onenote",
        "ms_graph_mcp.internal",
    ):
        try:
            mod = __import__(mod_name, fromlist=["*"])
        except Exception:
            continue
        if getattr(mod, "graph_get", None) is not None:
            mod.graph_get = _graph_get
        if original_post is not None and getattr(mod, "graph_post", None) is not None:
            mod.graph_post = _graph_post
        if getattr(mod, "graph_error_response", None) is not None:
            mod.graph_error_response = _graph_error_response

    _graph_client_patch_installed = True
    logger.info("Installed Graph MCP client To Do/$search/Teams-MSA patches")

def _install_graph_jwt_patches() -> None:
    """Fix Graph token validation for ChatUI's OAuth-forwarding posture.

    - Accept Graph URI + Graph app-id audiences
    - Accept issuers for the token's ``tid`` when admin tenant is ``common``
    - Accept opaque (non-JWT) Graph tokens Entra sometimes mints
    - Log reject detail + token meta (no raw token)
    """
    global _jwt_issuer_patch_installed
    if _jwt_issuer_patch_installed:
        return

    import jwt
    import ms_graph_mcp.auth as graph_auth_mod
    import ms_graph_mcp.entra.jwt_verify as jwt_mod
    import ms_graph_mcp.entra.middleware as entra_mw
    from jwt import PyJWKClientError
    from ms_graph_mcp.config import GraphMcpConfig
    from ms_graph_mcp.entra.claims import Principal
    from ms_graph_mcp.entra.errors import AuthError, InvalidTokenError

    original_to_auth = GraphMcpConfig.to_auth_config

    def _to_auth_config(self: Any) -> Any:
        auth = original_to_auth(self)
        # Prefer v1 common JWKS — Graph access tokens often use sts.windows.net
        # (v1) issuers and fail signature checks against discovery/v2.0/keys.
        tenant = str(getattr(self, "tenant_id", None) or "").strip().lower()
        updates: dict[str, Any] = {"audience": _GRAPH_AUDIENCES}
        if tenant in _MULTI_TENANT_IDS:
            updates["jwks_url"] = (
                "https://login.microsoftonline.com/common/discovery/keys"
            )
        return auth.model_copy(update=updates)

    def _jwks_urls_for_token(token: str, cfg: Any) -> list[str]:
        """Ordered JWKS endpoints to try (v1 Graph tokens ≠ v2-only keys)."""
        urls: list[str] = []

        def _add(url: str) -> None:
            if url and url not in urls:
                urls.append(url)

        _add(str(cfg.effective_jwks_url or "").strip())

        tid = ""
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
            tid = str(claims.get("tid") or "").strip()
        except Exception:
            pass

        cfg_tenant = str(getattr(cfg, "tenant_id", None) or "").strip()
        tenants: list[str] = []
        for candidate in (tid, cfg_tenant, "common"):
            if not candidate:
                continue
            if candidate.lower() in _MULTI_TENANT_IDS and candidate.lower() != "common":
                continue
            if candidate not in tenants:
                tenants.append(candidate)

        for tenant in tenants:
            _add(f"https://login.microsoftonline.com/{tenant}/discovery/keys")
            _add(
                f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
            )
        _add("https://login.microsoftonline.com/common/discovery/keys")
        _add("https://login.microsoftonline.com/common/discovery/v2.0/keys")
        return urls

    def _graph_audiences(cfg: Any) -> list[str]:
        audiences = list(cfg.audience_list or [])
        for extra in (
            "https://graph.microsoft.com",
            "00000003-0000-0000-c000-000000000000",
        ):
            if extra not in audiences:
                audiences.append(extra)
        return audiences

    def _validate_graph_claims(payload: dict, cfg: Any) -> dict:
        """aud / exp / iss / tid checks shared by signed + claim-only paths."""
        import time

        audiences = set(_graph_audiences(cfg))
        aud = payload.get("aud")
        aud_values = (
            {str(aud)}
            if isinstance(aud, str)
            else {str(x) for x in aud}
            if isinstance(aud, (list, tuple))
            else set()
        )
        if audiences and not (aud_values & audiences):
            raise InvalidTokenError(f"audience mismatch ({aud!r})")

        exp = payload.get("exp")
        if exp is not None:
            try:
                if time.time() > float(exp) + float(cfg.clock_skew_seconds):
                    raise InvalidTokenError("token expired")
            except (TypeError, ValueError) as exc:
                raise InvalidTokenError("token expired") from exc

        iss = str(payload.get("iss") or "")
        tid = str(payload.get("tid") or "")
        cfg_tenant = str(getattr(cfg, "tenant_id", None) or "").strip().lower()
        allowed = set(cfg.effective_issuers)
        if tid:
            allowed |= _issuers_for_tid(tid)
        if cfg_tenant and cfg_tenant not in _MULTI_TENANT_IDS:
            allowed |= _issuers_for_tid(cfg_tenant)
        if iss not in allowed:
            raise InvalidTokenError(
                f"issuer not recognized for the configured tenant ({iss})"
            )
        if (
            cfg_tenant
            and cfg_tenant not in _MULTI_TENANT_IDS
            and tid
            and tid.lower() != cfg_tenant
        ):
            raise InvalidTokenError(
                "token tenant does not match the configured directory"
            )
        return payload

    def _decode_verified(token: str, cfg: Any) -> dict:
        audiences = _graph_audiences(cfg)

        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:
            raise InvalidTokenError(f"malformed token header: {exc}") from exc
        alg = str(header.get("alg") or "RS256")
        algorithms = [alg] if alg in {"RS256", "PS256", "RS384", "RS512"} else ["RS256"]

        last_exc: Exception | None = None
        for jwks_url in _jwks_urls_for_token(token, cfg):
            try:
                client = jwt_mod.get_jwks_client(
                    jwks_url, cfg.jwks_cache_ttl_seconds
                )
                signing_key = client.get_signing_key_from_jwt(token)
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=algorithms,
                    audience=(audiences or None),
                    leeway=cfg.clock_skew_seconds,
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_aud": bool(audiences),
                        "verify_iss": False,
                    },
                )
                return _validate_graph_claims(payload, cfg)
            except jwt.ExpiredSignatureError as exp_exc:
                raise InvalidTokenError("token expired") from exp_exc
            except jwt.InvalidAudienceError as aud_exc:
                raise InvalidTokenError("audience mismatch") from aud_exc
            except (PyJWKClientError, jwt.InvalidTokenError, Exception) as exc:
                last_exc = exc
                continue

        # Graph access tokens are often "opaque to the client": Entra mints a JWT
        # that Graph accepts, but local JWKS signature checks still fail. ChatUI
        # only forwards tokens from its own OAuth store, so claim checks + Graph
        # API enforcement are enough for the embedded MCP.
        try:
            payload = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
            validated = _validate_graph_claims(payload, cfg)
        except InvalidTokenError:
            raise
        except Exception as exc:
            raise InvalidTokenError(
                f"token verification failed: {last_exc}"
            ) from last_exc

        logger.debug(
            "Graph MCP: claim-only token accept (%s; jwks_error=%s)",
            _token_debug_meta(token),
            last_exc,
        )
        return validated

    def _verify_token(token: str, cfg: Any) -> Principal:
        if not token:
            from ms_graph_mcp.entra.errors import MissingTokenError

            raise MissingTokenError("empty bearer token")

        # Opaque Entra access tokens (not JWTs) — Graph accepts them; we cannot
        # locally verify signature. Trust that ChatUI only forwards tokens from
        # its own OAuth store; still require a non-empty bearer.
        if token.count(".") != 2:
            logger.warning(
                "Graph MCP: opaque access token (%s) — skipping local JWT verify",
                _token_debug_meta(token),
            )
            azp = next(iter(cfg.allowed_azp_set), "") or str(
                getattr(cfg, "client_id", "") or ""
            )
            return Principal(
                subject_id="opaque",
                email="",
                tenant_id=str(getattr(cfg, "tenant_id", "") or ""),
                roles=frozenset(),
                azp=azp,
                is_app_only=False,
                raw={},
            )

        claims = (
            _decode_verified(token, cfg)
            if cfg.verify_signature
            else jwt_mod._decode_unverified(token, cfg)
        )
        if "nonce" in claims:
            raise InvalidTokenError(
                "ID token presented where an access token is required"
            )
        from ms_graph_mcp.entra.claims import extract_principal

        return extract_principal(claims)

    async def _dispatch_logged(self: Any, request: Any, call_next: Any) -> Any:
        from starlette.responses import JSONResponse

        path = request.url.path
        if path in graph_auth_mod._PUBLIC_PATHS or path.startswith(
            graph_auth_mod._PUBLIC_PATH_PREFIXES
        ):
            return await call_next(request)

        from ms_graph_mcp.entra import AuthMode
        from ms_graph_mcp.entra.middleware import authenticate_request

        try:
            principal = await authenticate_request(
                request, cfg=self._cfg, mode=AuthMode.DOWNSTREAM_SERVICE
            )
        except AuthError as exc:
            auth_header = request.headers.get("Authorization", "")
            token = (
                auth_header.removeprefix("Bearer ").strip()
                if auth_header.startswith("Bearer ")
                else ""
            )
            logger.warning(
                "ms-graph-mcp: rejected request (%s): %s [%s]",
                exc.reason,
                exc,
                _token_debug_meta(token),
            )
            headers: dict[str, str] = {}
            if exc.status_code == 401:
                challenge = graph_auth_mod._www_authenticate()
                if challenge:
                    headers["WWW-Authenticate"] = challenge
            return JSONResponse(
                {"error": str(exc)}, status_code=exc.status_code, headers=headers
            )

        # Duplicate the success path from GraphMcpAuthMiddleware.dispatch
        from ms_graph_mcp.context import current_request_context
        from ms_graph_mcp.entra.context import current_access_token

        graph_token = "" if principal.is_app_only else current_access_token.get("")
        internal_scope = principal.is_machine and (
            request.headers.get("X-Internal-Scope", "").lower() == "true"
        )
        obo_token = (
            request.headers.get("X-OBO-Token", "").strip() if internal_scope else ""
        )
        ctx: dict = {
            "access_token": obo_token or graph_token,
            "user_email": principal.email,
            "write_scope": request.headers.get("X-Write-Scope", "").lower() == "true",
            "internal_scope": internal_scope,
        }
        requested_toolsets = request.headers.get("X-Toolsets", "").strip()
        if requested_toolsets:
            ctx["toolsets"] = requested_toolsets
        entra_app_token = request.headers.get("X-Entra-App-Token", "")
        if entra_app_token:
            ctx["entra_app_token"] = entra_app_token
        current_request_context.set(ctx)
        return await call_next(request)

    jwt_mod._decode_verified = _decode_verified  # type: ignore[assignment]
    jwt_mod.verify_token = _verify_token  # type: ignore[assignment]
    # authenticate_request imported verify_token by name — patch that binding too.
    entra_mw.verify_token = _verify_token  # type: ignore[assignment]
    GraphMcpConfig.to_auth_config = _to_auth_config  # type: ignore[method-assign]
    graph_auth_mod.GraphMcpAuthMiddleware.dispatch = _dispatch_logged  # type: ignore[method-assign]
    _jwt_issuer_patch_installed = True
    logger.info("Installed Graph MCP JWT audience/issuer/opaque-token patches")


class _UnavailableApp:
    """ASGI app returned when Microsoft OAuth is not configured."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        response = JSONResponse(
            {"error": "Microsoft OAuth is not configured"},
            status_code=503,
        )
        await response(scope, receive, send)


class GraphMcpEmbed:
    """Hot-swappable embedded Graph MCP Starlette app."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._app: ASGIApp = _UnavailableApp()
        self._fingerprint: tuple[str, str] | None = None
        self._lifespan_cm: Any = None
        self._started = False
        self._invalidated = False

    def invalidate(self) -> None:
        """Mark the embedded app stale after OAuth config changes."""
        self._invalidated = True

    def _loop_lock(self) -> asyncio.Lock:
        """Lock bound to the running loop (created lazily for uvicorn's loop)."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _load_fingerprint(self) -> tuple[str, str] | None:
        from app.db.session import SessionLocal
        from app.services.integrations.store import resolve_effective_admin_config

        with SessionLocal() as session:
            cfg, _, enabled = resolve_effective_admin_config(
                session,
                target_kind="oauth_provider",
                target_id="microsoft",
                org_id=None,
            )
        if not enabled:
            return None
        client_id = str(cfg.get("client_id") or "").strip()
        if not client_id:
            return None
        tenant_id = str(cfg.get("tenant_id") or "common").strip() or "common"
        return client_id, tenant_id

    def _build_app(self, client_id: str, tenant_id: str) -> Starlette:
        from ms_graph_mcp.app import build_app
        from ms_graph_mcp.config import GraphMcpConfig

        _install_graph_jwt_patches()
        _install_graph_client_patches()
        resource_url = embedded_graph_mcp_url()
        cfg = GraphMcpConfig(
            client_id=client_id,
            tenant_id=tenant_id,
            toolsets="all",
            read_only=False,
            jwt_verify=True,
            mcp_does_obo=False,
            resource_url=resource_url,
            allowed_hosts=_allowed_hosts_for_embed(resource_url),
        )
        return build_app(cfg)

    async def _swap(self, fingerprint: tuple[str, str] | None) -> None:
        if self._lifespan_cm is not None:
            try:
                await self._lifespan_cm.__aexit__(None, None, None)
            except Exception:
                logger.exception("Failed to shut down previous Graph MCP app")
            self._lifespan_cm = None

        if fingerprint is None:
            self._app = _UnavailableApp()
            self._fingerprint = None
            self._invalidated = False
            return

        client_id, tenant_id = fingerprint
        app = self._build_app(client_id, tenant_id)
        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        self._app = app
        self._fingerprint = fingerprint
        self._lifespan_cm = cm
        self._invalidated = False
        logger.info(
            "Embedded Graph MCP ready (tenant=%s client_id=%s…)",
            tenant_id,
            client_id[:8],
        )

    async def ensure(self) -> None:
        # Bind/reset to this event loop before trusting _started.
        async with self._loop_lock():
            fingerprint = self._load_fingerprint()
            if (
                not self._invalidated
                and fingerprint == self._fingerprint
                and self._started
            ):
                return
            await self._swap(fingerprint)
            self._started = True

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        await self.ensure()
        try:
            yield
        finally:
            async with self._loop_lock():
                if self._lifespan_cm is not None:
                    try:
                        await self._lifespan_cm.__aexit__(None, None, None)
                    except Exception:
                        logger.exception("Failed to shut down Graph MCP app")
                    self._lifespan_cm = None
                self._app = _UnavailableApp()
                self._fingerprint = None
                self._started = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Lifespan is owned by FastAPI; ignore nested lifespan events.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        await self.ensure()
        await self._app(scope, receive, send)


graph_mcp_embed = GraphMcpEmbed()


def invalidate_graph_mcp_embed() -> None:
    graph_mcp_embed.invalidate()


def sync_microsoft_catalog_servers(session: Any) -> None:
    """Ensure all Microsoft catalog MCP rows point at the embedded URL."""
    from app.services.integrations.catalog import list_catalog, is_microsoft_catalog
    from app.services.integrations.materialize import ensure_catalog_mcp_server

    for entry in list_catalog():
        if not is_microsoft_catalog(entry.catalog_id):
            continue
        ensure_catalog_mcp_server(session, entry, mcp_url=embedded_graph_mcp_url())

