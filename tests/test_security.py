"""Access-control regressions against the same middleware and real application routes."""

import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from services.auth import AUTH_PATHS, COOKIE_NAME, AuthSettings, install_security
from tests.auth_client import TEST_PASSWORD

ORIGIN = "http://localhost:8000"
UNSAFE_ROUTES = [
    (next(iter(route.methods)), route.path)
    for route in main.app.routes
    if getattr(route, "methods", set()) & {"POST", "PUT", "DELETE", "PATCH"}
    and route.path not in AUTH_PATHS
]
PROTECTED_ROUTES = [
    (sorted(route.methods - {"HEAD"})[0], route.path)
    for route in main.app.routes
    if getattr(route, "methods", None) and route.path not in AUTH_PATHS
]


def path_for(path):
    return re.sub(r"\{([^}]+)\}", lambda match: "BTCUSDC" if match.group(1) == "symbol" else "1", path)


@pytest.fixture
def secured_app():
    def build(*, password_hash=None, origins=(ORIGIN,), destructive=False, seconds=3600):
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        # Reuse every actual endpoint; only session state/configuration is separate per test.
        app.router.routes.extend(route for route in main.app.routes if getattr(route, "path", "") not in AUTH_PATHS)
        app.exception_handlers.update(main.app.exception_handlers)
        install_security(app, main.templates, AuthSettings(
            password_hash=os.environ["CRYPTOAI_AUTH_PASSWORD_HASH"] if password_hash is None else password_hash,
            origins=origins, allow_destructive=destructive, session_seconds=seconds,
        ))
        return app
    return build


def login(client, password=TEST_PASSWORD):
    page = client.get("/login", follow_redirects=False)
    assert page.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post("/login", data={"password": password, "csrf_token": token}, follow_redirects=False)
    if response.status_code != 303:
        return response
    assert client.get("/api/auth/session").status_code == 200
    page = client.get("/")
    client.headers["X-CSRF-Token"] = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return response


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
def test_every_existing_route_refuses_anonymous_access(secured_app, method, path):
    client = TestClient(secured_app(), base_url=ORIGIN, follow_redirects=False)
    response = client.request(method, path_for(path))
    assert response.status_code == (401 if path.startswith("/api/") else 303)
    assert response.headers["cache-control"].startswith("no-store")


@pytest.mark.parametrize("method,path", UNSAFE_ROUTES)
def test_every_business_mutation_requires_csrf(secured_app, method, path):
    client = TestClient(secured_app(), base_url=ORIGIN)
    assert login(client).status_code == 303
    client.headers.pop("X-CSRF-Token")
    response = client.request(method, path_for(path), json={"note": "must not be saved"})
    assert response.status_code == 403


def test_authenticated_cockpit_and_personal_api_work(secured_app, monkeypatch):
    app = secured_app()
    client = TestClient(app, base_url=ORIGIN)
    assert login(client).status_code == 303
    monkeypatch.setattr(main, "list_positions", lambda: [{"symbol": "PRIVATEUSDC"}])
    assert client.get("/api/positions").json() == [{"symbol": "PRIVATEUSDC"}]
    page = client.get("/")
    assert page.status_code == 200
    assert 'name="csrf_token"' in page.text
    assert 'name="session-expires-in"' in page.text
    assert 'action="/logout"' in page.text
    assert 'id="adminResetButton" type="submit" disabled' in page.text


def test_not_configured_is_closed_without_default_password(secured_app):
    client = TestClient(secured_app(password_hash=""), base_url=ORIGIN, follow_redirects=False)
    assert client.get("/login").status_code == 503
    assert client.get("/api/positions").status_code == 401
    assert client.post("/login", json={"password": TEST_PASSWORD}).status_code == 503


@pytest.mark.parametrize("token", [None, "wrong-token", "\u00e9" * 43])
def test_login_itself_requires_session_bound_csrf(secured_app, token):
    client = TestClient(secured_app(), base_url=ORIGIN)
    assert client.get("/login").status_code == 200
    payload = {"password": TEST_PASSWORD}
    if token is not None:
        payload["csrf_token"] = token
    assert client.post("/login", data=payload).status_code == 403
    assert client.get("/api/auth/session").status_code == 401


def test_csrf_tokens_are_bound_to_each_session_and_login_rotates_them(secured_app):
    app = secured_app()
    one, two = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)
    before = one.get("/login")
    old_token = re.search(r'name="csrf_token" value="([^"]+)"', before.text).group(1)
    old_cookie = one.cookies.get(COOKIE_NAME)
    assert login(one).status_code == 303
    assert login(two).status_code == 303
    assert one.cookies.get(COOKIE_NAME) != old_cookie
    assert one.headers["X-CSRF-Token"] != old_token
    assert one.headers["X-CSRF-Token"] != two.headers["X-CSRF-Token"]
    replay = TestClient(app, base_url=ORIGIN)
    replay.cookies.set(COOKIE_NAME, old_cookie)
    assert replay.get("/api/watchlist").status_code == 401
    response = two.post("/api/watch-candidates", json={}, headers={"X-CSRF-Token": one.headers["X-CSRF-Token"]})
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Referer": "https://evil.example/path"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Host": "localhost.evil.example:8000"},
])
def test_foreign_origins_cannot_mutate_even_with_correct_csrf(secured_app, headers):
    client = TestClient(secured_app(), base_url=ORIGIN)
    assert login(client).status_code == 303
    assert client.post("/api/positions", json={}, headers=headers).status_code == 403


def test_logout_requires_csrf_and_revokes_replayed_cookie(secured_app):
    app = secured_app()
    client = TestClient(app, base_url=ORIGIN)
    assert login(client).status_code == 303
    stolen = client.cookies.get(COOKIE_NAME)
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/logout", data={}, follow_redirects=False).status_code == 415
    assert client.get("/api/watchlist").status_code == 200
    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "expires=Thu, 01 Jan 1970" in response.headers["set-cookie"]
    assert client.cookies.get(COOKIE_NAME) is None
    replay = TestClient(app, base_url=ORIGIN)
    replay.cookies.set(COOKIE_NAME, stolen)
    assert replay.get("/api/watchlist").status_code == 401


def test_expiry_is_absolute_and_cookie_tampering_fails_closed(secured_app):
    app = secured_app(seconds=60)
    app.state.auth.clock = lambda: 1000
    client = TestClient(app, base_url=ORIGIN)
    assert login(client).status_code == 303
    assert client.get("/api/watchlist").status_code == 200
    app.state.auth.clock = lambda: 1061
    response = client.get("/api/watchlist")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert "session a expire" in client.get("/login?expired=1").text
    bad = TestClient(app, base_url=ORIGIN)
    bad.cookies.set(COOKIE_NAME, "forged-session-cookie")
    assert bad.get("/api/watchlist").status_code == 401


@pytest.mark.parametrize("origin,secure", [(ORIGIN, False), ("https://localhost:8000", True)])
def test_cookie_flags_and_security_headers(secured_app, origin, secure):
    client = TestClient(secured_app(origins=(origin,)), base_url=origin)
    response = client.get("/login")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert ("; secure" in cookie) == secure
    assert "domain=" not in cookie
    for name in ("cache-control", "x-frame-options", "x-content-type-options", "referrer-policy"):
        assert name in response.headers
    assert response.headers["x-frame-options"] == "DENY"


def test_https_never_reissues_an_insecure_cookie_even_for_rejected_origin(secured_app):
    app = secured_app()
    client = TestClient(app, base_url=ORIGIN)
    client.get("/login")
    response = client.get("https://localhost:8000/login", follow_redirects=False)
    assert response.status_code == 403
    assert all("; secure" in value.lower() for value in response.headers.get_list("set-cookie"))


@pytest.mark.parametrize("route,confirmation", [
    ("/api/admin/reset", "RESET"), ("/api/admin/cleanup-invalid-symbols", "CLEANUP"),
])
@pytest.mark.parametrize("enabled,confirm,password,expected", [
    (False, True, True, 403), (True, False, True, 403),
    (True, True, False, 403), (True, True, True, 200),
])
def test_destructive_actions_require_flag_csrf_confirmation_and_password(
    secured_app, monkeypatch, route, confirmation, enabled, confirm, password, expected
):
    calls = []
    monkeypatch.setattr(main, "reset_database", lambda: calls.append("reset") or {})
    monkeypatch.setattr(main.scanner, "reset_scan_cache", lambda **kwargs: {})
    monkeypatch.setattr(main, "cleanup_invalid_symbols", lambda: calls.append("cleanup") or {"removed": 0})
    client = TestClient(secured_app(destructive=enabled), base_url=ORIGIN)
    assert login(client).status_code == 303
    response = client.post(route, json={
        "confirmation": confirmation if confirm else "",
        "password": TEST_PASSWORD if password else "wrong-password",
    })
    assert response.status_code == expected
    assert len(calls) == (1 if expected == 200 else 0)
    assert client.get(route).status_code == 405


def test_destructive_form_posts_password_without_javascript_and_redirects(secured_app, monkeypatch):
    client = TestClient(secured_app(destructive=True), base_url=ORIGIN, follow_redirects=False)
    assert login(client).status_code == 303
    monkeypatch.setattr(main, "reset_database", lambda: {})
    monkeypatch.setattr(main.scanner, "reset_scan_cache", lambda **kwargs: {})
    page = client.get("/")
    assert 'type="password" name="password"' in page.text
    response = client.post("/api/admin/reset", data={
        "csrf_token": client.headers["X-CSRF-Token"], "confirmation": "RESET", "password": TEST_PASSWORD,
    })
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_binance_sync_requires_session_and_csrf_before_any_private_work(secured_app, monkeypatch):
    calls = []
    monkeypatch.setattr(main.sync_jobs, "start", lambda **kwargs: calls.append(True) or {"accepted": True})
    app = secured_app()
    client = TestClient(app, base_url=ORIGIN)
    assert client.post("/api/binance/sync").status_code == 401
    assert not calls
    login(client)
    assert client.post("/api/binance/sync", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert not calls
    assert client.post("/api/binance/sync").json()["accepted"] is True
    assert calls == [True]


def test_password_attempts_are_limited_across_new_login_sessions(secured_app):
    app = secured_app()
    for attempt in range(6):
        response = login(TestClient(app, base_url=ORIGIN), password="wrong-password")
        assert response.status_code == (401 if attempt < 5 else 429)
    assert response.headers["retry-after"] == "60"
    app.state.auth.clock = lambda: 10**12
    assert login(TestClient(app, base_url=ORIGIN)).status_code == 303


def test_pending_session_flood_does_not_evict_authenticated_session(secured_app):
    app = secured_app()
    client = TestClient(app, base_url=ORIGIN)
    login(client)
    for _ in range(130):
        assert TestClient(app, base_url=ORIGIN).get("/login").status_code == 200
    assert len(app.state.auth.sessions) <= 129
    assert client.get("/api/watchlist").status_code == 200


def test_authentication_secrets_are_absent_from_bodies_and_logs(secured_app, caplog):
    app = secured_app()
    client = TestClient(app, base_url=ORIGIN)
    failed = login(client, password="wrong-password-sensitive-marker")
    assert failed.status_code == 401
    assert login(client).status_code == 303
    cookie = client.cookies.get(COOKIE_NAME)
    bodies = failed.text + client.get("/").text + client.get("/api/auth/session").text
    secrets = [TEST_PASSWORD, "wrong-password-sensitive-marker", app.state.auth.settings.password_hash,
               app.state.auth.signing_key, cookie, *app.state.auth.sessions.keys()]
    for secret in secrets:
        assert secret not in bodies
        assert secret not in caplog.text
    assert "password" not in client.get("/api/auth/session").json()


def test_backend_failures_do_not_echo_exception_secrets(secured_app, monkeypatch, caplog):
    client = TestClient(secured_app(), base_url=ORIGIN, raise_server_exceptions=False)
    login(client)
    marker = "sensitive-exception-marker"
    def fail(*args, **kwargs):
        raise RuntimeError(marker)
    monkeypatch.setattr(main.scanner, "analyze_symbol", fail)
    assert client.get("/api/analyze/BTCUSDC").status_code == 502
    monkeypatch.setattr(main, "list_positions", fail)
    response = client.get("/api/positions")
    assert response.status_code == 500
    assert marker not in response.text + caplog.text
    assert response.headers["cache-control"].startswith("no-store")


def test_new_routes_are_protected_by_default_and_static_traversal_is_denied(secured_app):
    app = secured_app()
    @app.get("/api/future-sensitive")
    def future():
        pytest.fail("Anonymous endpoint execution")
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get("/api/future-sensitive").status_code == 401
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/%2e%2e/.env").status_code in {401, 404}
    assert client.get("/static/%2e%2e/database/history.db").status_code in {401, 404}


@pytest.mark.parametrize("kwargs", [
    {"origins": ("http://0.0.0.0:8000",)},
    {"origins": ("http://localhost:8000/path",)},
    {"origins": ("https://name:password@example.test",)},
    {"origins": ("https://example.test", "http://localhost:8000")},
    {"session_seconds": 0}, {"password_hash": "plaintext-password"},
])
def test_unsafe_configuration_is_rejected_without_echoing_secrets(kwargs):
    with pytest.raises(ValueError) as error:
        AuthSettings(**kwargs)
    assert "plaintext-password" not in str(error.value)
    assert "name:password" not in str(error.value)


def test_transport_wrapper_enforces_secure_with_library_signing():
    from fastapi import Request
    from services.auth import SecureSessionMiddleware
    app = FastAPI()
    app.add_middleware(SecureSessionMiddleware, secret_key="test-only-cookie-signing-key",
                       session_cookie=COOKIE_NAME, same_site="strict", https_only=False)
    @app.get("/")
    def issue_cookie(request: Request):
        request.session["sid"] = "test-only-opaque-id"
        return {"ok": True}
    client = TestClient(app, base_url="https://localhost:8000")
    assert "; secure" in client.get("/").headers["set-cookie"].lower()


@pytest.mark.parametrize("route", ["/api/scan", "/api/market", "/api/ai-alerts", "/api/decisions",
                                   "/api/relative-strength/BTCUSDC"])
def test_legacy_get_routes_that_write_cache_or_history_require_csrf(secured_app, route):
    client = TestClient(secured_app(), base_url=ORIGIN)
    login(client)
    client.headers.pop("X-CSRF-Token")
    response = client.get(route)
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_REJECTED"


def test_browser_ajax_guard_needs_no_secret_and_does_not_authorize_sensitive_forms(secured_app, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "create_watch_candidate", lambda payload: calls.append(True) or {"id": 1})
    client = TestClient(secured_app(destructive=True), base_url=ORIGIN)
    login(client)
    client.headers.pop("X-CSRF-Token")
    client.headers["X-CSRF-Guard"] = "1"
    assert client.post("/api/watch-candidates", json={"symbol": "BTCUSDC"}).status_code == 200
    assert calls == [True]
    assert client.post("/api/watch-candidates", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert calls == [True]
    for route in ("/login", "/logout", "/api/admin/reset", "/api/admin/cleanup-invalid-symbols"):
        assert client.post(route, json={"password": TEST_PASSWORD}).status_code == 403
    status = client.get("/api/auth/session").json()
    assert "csrf_token" not in status


def test_foreign_preflight_cannot_enable_browser_ajax_guard(secured_app):
    client = TestClient(secured_app(), base_url=ORIGIN, follow_redirects=False)
    response = client.options("/api/positions", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-csrf-guard,content-type",
    })
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_validation_errors_do_not_echo_server_credentials(secured_app, monkeypatch, caplog):
    import logging
    client = TestClient(secured_app(), base_url=ORIGIN)
    login(client)
    key = "test-only-server-credential-sensitive-marker"
    monkeypatch.setenv("BINANCE_API_SECRET", key)
    caplog.set_level(logging.INFO, logger="crypto_ai.watchlist")
    def fail(payload):
        raise ValueError("Rejected input: " + key)
    monkeypatch.setattr(main, "create_watch_candidate", fail)
    response = client.post("/api/watch-candidates", json={"symbol": "BTCUSDC"})
    assert response.status_code == 400
    assert "[redacted]" in response.json()["error"]
    assert key not in response.text + caplog.text


def test_password_hash_helper_never_echoes_plaintext(monkeypatch, capsys):
    from tools import hash_password
    from pwdlib import PasswordHash
    values = iter([TEST_PASSWORD, TEST_PASSWORD])
    monkeypatch.setattr(hash_password, "getpass", lambda prompt: next(values))
    hash_password.main()
    output = capsys.readouterr().out.strip()
    assert output.startswith("$argon2id$")
    assert TEST_PASSWORD not in output
    assert PasswordHash.recommended().verify(TEST_PASSWORD, output)
