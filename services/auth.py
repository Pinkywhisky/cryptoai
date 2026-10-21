"""Single-user web authentication; credentials and revocable sessions stay server-side."""

from __future__ import annotations

import os
import asyncio
import json
import uuid
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from services.api_models import Credentials
from services.login_limiter import LoginLimiter, address_key
from services.security_logging import security_event
from services.trusted_proxy import TrustedProxyMiddleware, trusted_network

COOKIE_NAME = "cryptoai_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
AUTH_PATHS = {"/login", "/logout", "/api/auth/session"}
FORM_CSRF_PATHS = {"/login", "/logout", "/api/admin/reset", "/api/admin/cleanup-invalid-symbols"}
STATEFUL_GET_PATHS = {"/scan", "/api/scan", "/api/market", "/api/ai-alerts", "/api/decisions"}

SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; script-src-attr 'none'; "
        "style-src 'self'; style-src-attr 'none'; img-src 'self'; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; base-uri 'none'; worker-src 'none'; manifest-src 'none'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def redact_error_message(message: str) -> str:
    # Keep useful validation messages, without echoing server credential values.
    for name, value in os.environ.items():
        if len(value) >= 8 and any(part in name.upper() for part in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD")):
            message = message.replace(value, "[redacted]")
    return message


def origin_of(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid application origin")
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    port = parsed.port
    suffix = f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else ""
    return f"{parsed.scheme}://{host}{suffix}"


@dataclass(frozen=True)
class AuthSettings:
    password_hash: str = field(default="", repr=False)
    origins: tuple[str, ...] = ("http://127.0.0.1:8000", "http://localhost:8000")
    session_seconds: int = 3600
    allow_destructive: bool = False
    production: bool = False
    trusted_proxies: tuple[str, ...] = ()

    def __post_init__(self):
        if not 60 <= self.session_seconds <= 86400:
            raise ValueError("Session duration must be between 60 and 86400 seconds")
        if not self.origins or len({urlsplit(value).scheme for value in self.origins}) != 1:
            raise ValueError("Configure origins using a single HTTP or HTTPS scheme")
        for value in self.origins:
            parsed = urlsplit(value)
            if origin_of(value) != value or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("Configure exact origins without paths or credentials")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("HTTPS is required outside loopback")
        if self.production and (not self.secure_cookie or not self.password_hash):
            raise ValueError("Production requires HTTPS and configured authentication")
        for value in self.trusted_proxies:
            try:
                trusted_network(value)
            except ValueError:
                raise ValueError("Configure explicit trusted proxy addresses or networks") from None
        if self.password_hash and not self.password_hash.startswith("$argon2id$"):
            raise ValueError("CRYPTOAI_AUTH_PASSWORD_HASH must use Argon2id")

    @classmethod
    def from_env(cls):
        environment = os.getenv("CRYPTOAI_ENV", "development")
        if environment not in {"development", "production"}:
            raise ValueError("CRYPTOAI_ENV must be development or production")
        return cls(
            production=environment == "production",
            trusted_proxies=tuple(part.strip() for part in os.getenv("CRYPTOAI_TRUSTED_PROXIES", "").split(",") if part.strip()),
            password_hash=os.getenv("CRYPTOAI_AUTH_PASSWORD_HASH", ""),
            origins=tuple(part.strip() for part in os.getenv(
                "CRYPTOAI_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
            ).split(",") if part.strip()),
            session_seconds=int(os.getenv("CRYPTOAI_SESSION_SECONDS", "3600")),
            allow_destructive=os.getenv("CRYPTOAI_ALLOW_DESTRUCTIVE", "") == "1",
        )

    @property
    def secure_cookie(self):
        return self.origins[0].startswith("https://")


@dataclass(frozen=True)
class Session:
    csrf: str = field(repr=False)
    expires: float
    authenticated: bool
    issuer: str = field(default="unknown", repr=False)


class AuthService:
    def __init__(self, settings: AuthSettings, clock=time.monotonic):
        self.settings = settings
        self.clock = clock
        self.signing_key = secrets.token_urlsafe(48)
        self.passwords = PasswordHash.recommended()
        self.sessions: dict[str, Session] = {}
        self.limiter = LoginLimiter(lambda: self.clock())
        self.lock = threading.Lock()

    def session(self, request: Request) -> Session | None:
        sid = request.session.get("sid")
        if not isinstance(sid, str):
            return None
        with self.lock:
            value = self.sessions.get(sid)
            if value and value.expires <= self.clock():
                self.sessions.pop(sid, None)
                return None
            return value

    def revoke(self, request: Request):
        sid = request.session.get("sid")
        with self.lock:
            if isinstance(sid, str):
                self.sessions.pop(sid, None)
        request.session.clear()

    def issue(self, request: Request, *, authenticated: bool) -> Session:
        self.revoke(request)
        now = self.clock()
        sid = secrets.token_urlsafe(32)
        record = Session(secrets.token_urlsafe(32),
                         now + (self.settings.session_seconds if authenticated else min(300, self.settings.session_seconds)),
                         authenticated, address_key(request.client.host if request.client else "unknown"))
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value.expires > now}
            if not authenticated:
                local_pending = [key for key, value in self.sessions.items()
                                 if not value.authenticated and value.issuer == record.issuer]
                if len(local_pending) >= 8:
                    self.sessions.pop(local_pending[0], None)
            peers = [key for key, value in self.sessions.items() if value.authenticated == authenticated]
            if len(peers) >= 128:
                self.sessions.pop(peers[0], None)
            self.sessions[sid] = record
        request.session["sid"] = sid
        return record

    def verify_password(self, password: str) -> bool:
        # Admission and per-address budgets are reserved before entering the pool.
        if not isinstance(password, str) or len(password) > 1024 or not self.settings.password_hash:
            return False
        try:
            return self.passwords.verify(password, self.settings.password_hash)
        except Exception:
            return False


async def verify_credential(request: Request, password: str) -> bool:
    service = request.app.state.auth
    address = request.client.host if request.client else "unknown"
    try:
        delay = service.limiter.reserve(address)
        if delay:
            await asyncio.sleep(delay)
        def verify_in_worker():
            # The worker owns its slot, even if the awaiting request is cancelled.
            service.limiter.acquire()
            try:
                return service.verify_password(password)
            finally:
                service.limiter.release()
        valid = await run_in_threadpool(verify_in_worker)
        service.limiter.finish(address, valid)
    except HTTPException as exc:
        security_event(request, "login_throttled", exc.status_code)
        raise
    security_event(request, "credential_accepted" if valid else "credential_rejected", 200 if valid else 401)
    return valid


def parse_json(body):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result
    return json.loads(body, object_pairs_hook=unique_fields)


async def credential_payload(request: Request, *, validate: bool = True) -> dict:
    if hasattr(request.state, "auth_form"):
        return validate_credentials(request.state.auth_form) if validate else request.state.auth_form
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, "Requete trop volumineuse", headers=SECURITY_HEADERS)
    if request.headers.get("content-type", "").split(";")[0] == "application/x-www-form-urlencoded":
        try:
            fields = parse_qs(body.decode("utf-8"), max_num_fields=8, keep_blank_values=True)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(400, "Formulaire invalide", headers=SECURITY_HEADERS) from None
        if any(len(values) != 1 for values in fields.values()):
            raise HTTPException(400, "Formulaire invalide", headers=SECURITY_HEADERS)
        return validate_credentials({key: values[0] for key, values in fields.items()}) if validate else {key: values[0] for key, values in fields.items()}
    if request.headers.get("content-type", "").split(";")[0] == "application/json":
        try:
            payload = parse_json(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(400, "Requete invalide", headers=SECURITY_HEADERS) from None
        if isinstance(payload, dict):
            return validate_credentials(payload) if validate else payload
    raise HTTPException(415, "Format de requete non accepte", headers=SECURITY_HEADERS)


def validate_credentials(payload):
    try:
        return Credentials.model_validate(payload).payload()
    except ValidationError:
        raise HTTPException(422, "Requete invalide", headers=SECURITY_HEADERS) from None


class SecureSessionMiddleware(SessionMiddleware):
    """Keep Starlette signing; enforce Secure on every HTTPS Set-Cookie response."""

    async def __call__(self, scope, receive, send):
        async def secure_cookie_send(message):
            if scope["type"] == "http" and scope.get("scheme") == "https" and message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value + b"; secure"
                     if name.lower() == b"set-cookie" and value.startswith((COOKIE_NAME + "=").encode())
                     and b"; secure" not in value.lower() else value)
                    for name, value in message["headers"]
                ]
            await send(message)
        await super().__call__(scope, receive, secure_cookie_send)


class AccessMiddleware:
    """Deny by default, including legacy routes, docs and future endpoints."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        auth = request.app.state.auth
        request.state.request_id = uuid.uuid4().hex

        response_started = False
        async def secure_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers.append("Vary", "Cookie")
                headers["X-Request-ID"] = request.state.request_id
                if request.scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        async def deny(status, message, code):
            security_event(request, code.lower(), status)
            await JSONResponse({"success": False, "error": message, "code": code}, status_code=status)(
                scope, receive, secure_send
            )

        single_headers = {b"host", b"origin", b"referer", b"x-csrf-token", b"x-csrf-guard", b"content-length", b"cookie"}
        counts = {}
        for name, _ in scope.get("headers", []):
            key = name.lower()
            if key in single_headers:
                counts[key] = counts.get(key, 0) + 1
        if any(value > 1 for value in counts.values()):
            await deny(400, "En-tetes ambigus", "INVALID_HEADERS")
            return
        host = request.headers.get("host", "")
        if any(char in host for char in "/?#@\\ "+chr(9)+chr(10)+chr(13)):
            await deny(403, "Hote non autorise", "ORIGIN_REJECTED")
            return
        length = request.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
                if size < 0:
                    raise ValueError
            except ValueError:
                await deny(400, "Longueur de requete invalide", "INVALID_REQUEST")
                return
            if size > 65536:
                await deny(413, "Requete trop volumineuse", "REQUEST_TOO_LARGE")
                return
        try:
            target = origin_of(str(request.url))
        except ValueError:
            target = None
        if target not in auth.settings.origins:
            await deny(403, "Origine de requete non autorisee", "ORIGIN_REJECTED")
            return
        path, method = request.url.path, request.method
        public = path == "/login" or (path.startswith("/static/") and method in {"GET", "HEAD"})
        record = auth.session(request)
        request.state.auth_session = record
        if not public and (not record or not record.authenticated):
            request.session.clear()
            if path.startswith("/api/"):
                await deny(401, "Connexion requise ou session expiree", "AUTHENTICATION_REQUIRED")
            else:
                await RedirectResponse("/login", status_code=303)(scope, receive, secure_send)
            return
        mutating_get = method == "GET" and (
            path in STATEFUL_GET_PATHS or path.startswith("/api/relative-strength/")
        )
        if method not in SAFE_METHODS or mutating_get:
            if not auth.settings.password_hash:
                await deny(503, "Acces personnel non configure", "AUTH_NOT_CONFIGURED")
                return
            source = request.headers.get("origin") or request.headers.get("referer")
            try:
                source_matches = source is None or (origin_of(source) == target and (not request.headers.get("origin") or source == target))
            except ValueError:
                source_matches = False
            if not source_matches or request.headers.get("sec-fetch-site") == "cross-site":
                await deny(403, "Origine de requete non autorisee", "ORIGIN_REJECTED")
                return
            token = request.headers.get("x-csrf-token")
            if token is None and path in FORM_CSRF_PATHS:
                try:
                    request.state.auth_form = await credential_payload(request, validate=False)
                    token = request.state.auth_form.get("csrf_token")
                except HTTPException as exc:
                    await deny(exc.status_code, str(exc.detail), "INVALID_REQUEST")
                    return
            # A non-simple header requires a successful CORS preflight from foreign
            # browser origins. No CORS permissions are granted by this application.
            ajax_guard = path.startswith("/api/") and path not in FORM_CSRF_PATHS and request.headers.get("x-csrf-guard") == "1"
            valid_token = bool(record and isinstance(token, str) and token.isascii() and secrets.compare_digest(token, record.csrf))
            if not record or not (valid_token or ajax_guard):
                await deny(403, "Protection CSRF : rechargez la page", "CSRF_REJECTED")
                return
        if method not in SAFE_METHODS and path in (FORM_CSRF_PATHS - {"/logout"}) and not hasattr(request.state, "auth_form"):
            try:
                request.state.auth_form = await credential_payload(request, validate=False)
            except HTTPException as exc:
                await deny(exc.status_code, str(exc.detail), "INVALID_REQUEST")
                return
        if method not in SAFE_METHODS and not hasattr(request.state, "auth_form"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    await deny(413, "Requete trop volumineuse", "REQUEST_TOO_LARGE")
                    return
            if body and request.headers.get("content-type", "").split(";")[0] == "application/json":
                try:
                    parse_json(body)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    await deny(422, "Invalid request payload", "INVALID_REQUEST")
                    return
            sent = False
            original_receive = receive
            async def replay_body():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()
            receive = replay_body
        try:
            await self.app(scope, receive, secure_send)
        except Exception as exc:
            request.state.route_template = getattr(scope.get("route"), "path", None)
            security_event(request, "unhandled_exception", 500, exc)
            if not response_started:
                if path.startswith("/api/"):
                    response = JSONResponse({"success": False, "error": "Internal server error"}, status_code=500)
                else:
                    from starlette.responses import HTMLResponse
                    response = HTMLResponse("Erreur interne. Reessayez plus tard.", status_code=500)
                await response(scope, receive, secure_send)
            # If streaming has already started, return without sending an
            # invalid Content-Length terminator. Uvicorn closes the incomplete
            # response with a generic message, without a chained traceback.
            # Handle here: Starlette's outer ServerErrorMiddleware otherwise
            # re-raises to Uvicorn, which logs the exception message and traceback.


def install_security(app, templates, settings: AuthSettings | None = None):
    auth = AuthService(settings or AuthSettings.from_env())
    app.state.auth = auth
    docs = {"/docs", "/docs/oauth2-redirect", "/redoc"}
    app.router.routes[:] = [route for route in app.router.routes
                           if getattr(route, "path", "") not in docs
                           and not (auth.settings.production and getattr(route, "path", "") == "/openapi.json")]
    if not auth.settings.production:
        async def local_schema():
            return RedirectResponse("/openapi.json", status_code=303)
        for path in sorted(docs):
            app.add_api_route(path, local_schema, methods=["GET"], include_in_schema=False)
    app.add_middleware(AccessMiddleware)
    app.add_middleware(SecureSessionMiddleware, secret_key=auth.signing_key, session_cookie=COOKIE_NAME,
                       max_age=auth.settings.session_seconds, same_site="strict",
                       https_only=auth.settings.secure_cookie)
    app.add_middleware(TrustedProxyMiddleware, networks=auth.settings.trusted_proxies)

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        service = request.app.state.auth
        record = service.session(request)
        if record and record.authenticated:
            return RedirectResponse("/", status_code=303)
        configured = bool(service.settings.password_hash)
        if configured and not record:
            record = service.issue(request, authenticated=False)
        return templates.TemplateResponse(request=request, name="login.html", status_code=200 if configured else 503,
                                          context={"configured": configured, "csrf_token": record.csrf if record else "",
                                                   "error": "", "expired": request.query_params.get("expired") == "1"})

    @app.post("/login", include_in_schema=False)
    async def login(request: Request):
        service = request.app.state.auth
        payload = await credential_payload(request)
        try:
            valid = await verify_credential(request, payload.get("password", ""))
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
            record = service.session(request) or service.issue(request, authenticated=False)
            return templates.TemplateResponse(request=request, name="login.html", status_code=429,
                headers={"Retry-After": exc.headers["Retry-After"]},
                context={"configured": True, "csrf_token": record.csrf,
                         "error": "Trop de tentatives ou connexion occupee. Patientez " + exc.headers["Retry-After"] + " secondes, puis reessayez.",
                         "expired": False})
        if not valid:
            record = service.session(request) or service.issue(request, authenticated=False)
            return templates.TemplateResponse(request=request, name="login.html", status_code=401,
                                              context={"configured": True, "csrf_token": record.csrf,
                                                       "error": "Mot de passe incorrect.", "expired": False})
        service.issue(request, authenticated=True)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        request.app.state.auth.revoke(request)
        security_event(request, "logout", 303)
        return RedirectResponse("/login", status_code=303)

    @app.get("/api/auth/session", include_in_schema=False)
    async def session_status(request: Request):
        service = request.app.state.auth
        record = request.state.auth_session
        return {"authenticated": True,
                "expires_in": max(0, int(record.expires - service.clock())),
                "destructive_enabled": service.settings.allow_destructive}


async def authorize_destructive(request: Request, action: str):
    service = request.app.state.auth
    if not service.settings.allow_destructive:
        raise HTTPException(403, "Operations destructives desactivees", headers=SECURITY_HEADERS)
    payload = await credential_payload(request)
    if payload.get("confirmation") != action or not await verify_credential(request, payload.get("password", "")):
        raise HTTPException(403, "Confirmation et mot de passe requis", headers=SECURITY_HEADERS)
