"""Single-user web authentication; credentials and revocable sessions stay server-side."""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse

COOKIE_NAME = "cryptoai_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
AUTH_PATHS = {"/login", "/logout", "/api/auth/session"}
FORM_CSRF_PATHS = {"/login", "/logout", "/api/admin/reset", "/api/admin/cleanup-invalid-symbols"}
STATEFUL_GET_PATHS = {"/api/scan", "/api/market", "/api/ai-alerts", "/api/decisions"}

SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'",
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
        if self.password_hash and not self.password_hash.startswith("$argon2id$"):
            raise ValueError("CRYPTOAI_AUTH_PASSWORD_HASH must use Argon2id")

    @classmethod
    def from_env(cls):
        return cls(
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


class AuthService:
    def __init__(self, settings: AuthSettings, clock=time.monotonic):
        self.settings = settings
        self.clock = clock
        self.signing_key = secrets.token_urlsafe(48)
        self.passwords = PasswordHash.recommended()
        self.sessions: dict[str, Session] = {}
        self.attempts: deque[float] = deque()
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
                         authenticated)
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value.expires > now}
            peers = [key for key, value in self.sessions.items() if value.authenticated == authenticated]
            if len(peers) >= 128:
                self.sessions.pop(peers[0], None)
            self.sessions[sid] = record
        request.session["sid"] = sid
        return record

    def verify_password(self, password: str) -> bool:
        now = self.clock()
        with self.lock:
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                raise HTTPException(429, "Trop de tentatives. Reessayez dans une minute.",
                                    headers={"Retry-After": "60", **SECURITY_HEADERS})
            self.attempts.append(now)
        if not isinstance(password, str) or len(password) > 1024 or not self.settings.password_hash:
            return False
        try:
            valid = self.passwords.verify(password, self.settings.password_hash)
        except Exception:
            # Never log passwords, hashes, signed cookies or library exception details.
            valid = False
        if valid:
            with self.lock:
                self.attempts.clear()
        return valid


async def credential_payload(request: Request) -> dict:
    if hasattr(request.state, "auth_form"):
        return request.state.auth_form
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, "Requete trop volumineuse", headers=SECURITY_HEADERS)
    if request.headers.get("content-type", "").split(";")[0] == "application/x-www-form-urlencoded":
        try:
            fields = parse_qs(body.decode("utf-8"), max_num_fields=8)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Formulaire invalide", headers=SECURITY_HEADERS) from None
        if any(len(values) != 1 for values in fields.values()):
            raise HTTPException(400, "Formulaire invalide", headers=SECURITY_HEADERS)
        return {key: values[0] for key, values in fields.items()}
    if request.headers.get("content-type", "").split(";")[0] == "application/json":
        import json
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Requete invalide", headers=SECURITY_HEADERS) from None
        if isinstance(payload, dict):
            return payload
    raise HTTPException(415, "Format de requete non accepte", headers=SECURITY_HEADERS)


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

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers.append("Vary", "Cookie")
            await send(message)

        async def deny(status, message, code):
            await JSONResponse({"success": False, "error": message, "code": code}, status_code=status)(
                scope, receive, secure_send
            )

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
                source_matches = source is None or origin_of(source) == target
            except ValueError:
                source_matches = False
            if not source_matches or request.headers.get("sec-fetch-site") == "cross-site":
                await deny(403, "Origine de requete non autorisee", "ORIGIN_REJECTED")
                return
            token = request.headers.get("x-csrf-token")
            if token is None and path in FORM_CSRF_PATHS:
                try:
                    request.state.auth_form = await credential_payload(request)
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
        await self.app(scope, receive, secure_send)


def install_security(app, templates, settings: AuthSettings | None = None):
    auth = AuthService(settings or AuthSettings.from_env())
    app.state.auth = auth
    app.add_middleware(AccessMiddleware)
    app.add_middleware(SecureSessionMiddleware, secret_key=auth.signing_key, session_cookie=COOKIE_NAME,
                       max_age=auth.settings.session_seconds, same_site="strict",
                       https_only=auth.settings.secure_cookie)

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
        if not await run_in_threadpool(service.verify_password, payload.get("password", "")):
            record = service.session(request) or service.issue(request, authenticated=False)
            return templates.TemplateResponse(request=request, name="login.html", status_code=401,
                                              context={"configured": True, "csrf_token": record.csrf,
                                                       "error": "Mot de passe incorrect.", "expired": False})
        service.issue(request, authenticated=True)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        request.app.state.auth.revoke(request)
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
    if payload.get("confirmation") != action or not await run_in_threadpool(
        service.verify_password, payload.get("password", "")
    ):
        raise HTTPException(403, "Confirmation et mot de passe requis", headers=SECURITY_HEADERS)
