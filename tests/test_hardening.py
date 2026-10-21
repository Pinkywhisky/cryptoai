"""Adversarial tests for lot 2B; the root conftest denies network and personal DBs."""

import asyncio
import concurrent.futures
import json
import os
import threading
from dataclasses import replace

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import main
from services.api_models import PositionCreate, WatchCreate, JournalCreate, Credentials
from services.auth import AuthService, AuthSettings, SECURITY_HEADERS
from services.login_limiter import LoginLimiter, address_key
from services.trusted_proxy import TrustedProxyMiddleware
from tests.test_security import ORIGIN, TEST_PASSWORD, login, secured_app


@pytest.fixture
def client(secured_app):
    client = TestClient(secured_app(), base_url=ORIGIN, raise_server_exceptions=False)
    assert login(client).status_code == 303
    return client


@pytest.fixture
def fake_market(monkeypatch):
    import services.personal_data as personal
    def resolve(symbol):
        normalized = symbol.upper().replace("/", "").replace("-", "")
        return {"valid": True, "symbol": normalized, "resolved_symbol": normalized,
                "base_asset": normalized.removesuffix("USDC"), "market_source": "BINANCE_SPOT"}
    monkeypatch.setattr(personal, "resolve_market_symbol", resolve)
    monkeypatch.setattr(personal, "get_market_snapshot", lambda symbol: {
        "symbol": symbol, "analysis_available": False, "is_valid_symbol": True,
        "current_price": 11, "market_source": "BINANCE_SPOT",
    })
    monkeypatch.setattr(personal, "_build_journal_ai_snapshot", lambda symbol: {
        "resolution": resolve(symbol), "market": {"current_price": 11},
        "decision": {}, "market_regime": {},
    })


def test_complete_personal_workflow_and_revocation(client, fake_market, monkeypatch):
    marker = '<img src=x onerror="globalThis.compromised=1"> & Unicode \u00e9'
    position = client.post("/api/positions", json={
        "symbol": "btc/usdc", "quantity": 2, "average_buy_price": 10, "invested_amount": 20,
        "note": marker,
    })
    assert position.status_code == 200
    pid = position.json()["id"]
    assert position.json()["symbol"] == "BTCUSDC"
    assert client.put(f"/api/positions/{pid}", json={"note": "updated", "stop_loss": 8}).status_code == 200
    assert client.get("/api/positions").json()[0]["note"] == "updated"
    watch = client.post("/api/watch-candidates", json={"symbol": "ETHUSDC", "priority": "HIGH", "note": marker})
    assert watch.status_code == 200
    wid = watch.json()["id"]
    assert client.put(f"/api/watch-candidates/{wid}", json={"priority": "LOW", "target_buy_price": 9}).status_code == 200
    assert client.get("/api/watch-candidates").json()[0]["note"] == marker
    journal = client.post("/api/journal", json={"symbol": "BTCUSDC", "reason": marker, "confidence": 75})
    assert journal.status_code == 200
    jid = journal.json()["id"]
    assert client.put(f"/api/journal/{jid}", json={"result_status": "LOSS", "pnl_percent": -2.5}).status_code == 200
    assert client.get("/api/journal").json()[0]["reason"] == marker
    assert client.get("/api/journal/analytics").status_code == 200
    assert client.get("/").status_code == 200
    monkeypatch.setattr(main.sync_jobs, "start", lambda **kwargs: {"accepted": True, "read_only_mode": True})
    assert client.post("/api/binance/sync").json()["accepted"] is True
    assert client.delete(f"/api/watch-candidates/{wid}").status_code == 200
    assert client.get("/api/watch-candidates").json() == []
    assert client.delete(f"/api/positions/{pid}").status_code == 200
    assert client.delete(f"/api/journal/{jid}").status_code == 200
    cookie = client.cookies.get("cryptoai_session")
    assert client.post("/logout", follow_redirects=False).status_code == 303
    client.cookies.set("cryptoai_session", cookie)
    assert client.get("/api/positions").status_code == 401


INVALID_WRITES = [
    ("/api/positions", {"symbol": "BTCUSDC", "quantity": -1, "average_buy_price": 10, "invested_amount": 10}),
    ("/api/positions", {"symbol": "BTCUSDC", "quantity": True, "average_buy_price": 10, "invested_amount": 10}),
    ("/api/positions", {"symbol": "BTCUSDC", "quantity": "1", "average_buy_price": 10, "invested_amount": 10}),
    ("/api/positions", {"symbol": "BTCUSDC", "quantity": None, "average_buy_price": 10, "invested_amount": 10}),
    ("/api/positions", {"symbol": "BTCUSDC"}),
    ("/api/positions", {"symbol": "<img src=x>", "quantity": 1, "average_buy_price": 10, "invested_amount": 10}),
    ("/api/watch-candidates", {"symbol": "BTCUSDC", "priority": "ROOT"}),
    ("/api/watch-candidates", {"symbol": "BTCUSDC", "priority": None}),
    ("/api/watch-candidates", {"symbol": "BTCUSDC", "target_buy_price": -1}),
    ("/api/watch-candidates", {"symbol": "BTCUSDC", "target_buy_price": "1"}),
    ("/api/watch-candidates", {"symbol": "BTCUSDC", "unexpected_admin": True}),
    ("/api/watch-candidates", {"symbol": "A" * 41}),
    ("/api/watch-candidates", {"symbol": "BTCUSDC", "note": "x" * 4001}),
    ("/api/journal", {"symbol": "BTCUSDC", "confidence": 101}),
    ("/api/journal", {"symbol": "BTCUSDC", "confidence": 1.5}),
    ("/api/journal", {"symbol": "BTCUSDC", "duration_minutes": -1}),
    ("/api/journal", {"symbol": "BTCUSDC", "emotion": "UNKNOWN"}),
    ("/api/journal", {"symbol": "BTCUSDC", "trade_type": "UNKNOWN"}),
    ("/api/journal", {"symbol": "BTCUSDC", "result_status": "UNKNOWN"}),
    ("/api/journal", {"symbol": "BTCUSDC", "result": "WIN", "result_status": "LOSS"}),
    ("/api/journal", {"symbol": "BTCUSDC", "action": "BUY", "decision_type": "SELL"}),
]


@pytest.mark.parametrize("path,payload", INVALID_WRITES)
def test_invalid_write_is_422_without_any_persistence(client, path, payload, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid payload reached a business service")
    for name in ("create_position", "create_watch_candidate", "create_journal_entry"):
        monkeypatch.setattr(main, name, forbidden)
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert response.json() == {"success": False, "error": "Invalid request payload"}


@pytest.mark.parametrize("path,field", [
    ("/api/positions", "quantity"), ("/api/positions", "average_buy_price"),
    ("/api/watch-candidates", "target_buy_price"), ("/api/journal", "pnl_usdc"),
])
@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_never_reaches_storage(client, path, field, invalid, monkeypatch):
    monkeypatch.setattr(main, "create_position", lambda data: pytest.fail("Invalid number persisted"))
    monkeypatch.setattr(main, "create_watch_candidate", lambda data: pytest.fail("Invalid number persisted"))
    monkeypatch.setattr(main, "create_journal_entry", lambda data: pytest.fail("Invalid number persisted"))
    body = '{"symbol":"BTCUSDC","quantity":1,"average_buy_price":10,"invested_amount":10}' if path == "/api/positions" else '{"symbol":"BTCUSDC"}'
    data = json.loads(body)
    data.pop(field, None)
    body = json.dumps(data)[:-1] + "," + json.dumps(field) + ":" + invalid + "}"
    assert client.post(path, content=body, headers={"Content-Type": "application/json"}).status_code == 422


@pytest.mark.parametrize("kind,field,value", [
    ("positions", "quantity", None), ("positions", "average_buy_price", -1),
    ("watch-candidates", "symbol", None), ("watch-candidates", "priority", "EXTREME"),
    ("journal", "symbol", None), ("journal", "confidence", False),
])
def test_invalid_partial_update_is_atomic(client, fake_market, kind, field, value):
    payload = {"symbol": "BTCUSDC"}
    if kind == "positions":
        payload.update(quantity=2, average_buy_price=10, invested_amount=20)
    created = client.post("/api/" + kind, json=payload).json()
    before = client.get("/api/" + kind).json()
    response = client.put(f"/api/{kind}/{created['id']}", json={"note" if kind != "journal" else "notes": "must not persist", field: value})
    assert response.status_code == 422
    assert client.get("/api/" + kind).json() == before


@pytest.mark.parametrize("kind", ["positions", "watch-candidates", "journal"])
@pytest.mark.parametrize("identifier", ["0", "-1", "9223372036854775808", "abc"])
def test_invalid_identifiers_are_rejected_before_service(client, kind, identifier, monkeypatch):
    assert client.delete(f"/api/{kind}/{identifier}").status_code == 422


def test_partial_update_keeps_omitted_fields_and_zero_cost_positions(client, fake_market):
    created = client.post("/api/positions", json={"symbol":"BTCUSDC", "quantity":1, "average_buy_price":0, "invested_amount":0, "stop_loss":3}).json()
    result = client.put(f"/api/positions/{created['id']}", json={"note":"ok", "stop_loss":None})
    assert result.status_code == 200
    assert result.json()["quantity"] == 1 and result.json()["invested_amount"] == 0
    assert result.json()["stop_loss"] is None


def test_request_limits_apply_to_declared_and_streamed_bodies(client):
    body = b'{"note":"' + b"x" * 70000 + b'"}'
    assert client.put("/api/positions/1", content=body).status_code == 413
    assert client.put("/api/positions/1", content=iter([body[:50000], body[50000:]])).status_code == 413


@pytest.mark.parametrize("headers", [
    [("Origin", ORIGIN), ("Origin", "https://evil.example")],
    [("Host", "localhost:8000"), ("Host", "evil.example")],
    [("X-CSRF-Guard", "1"), ("X-CSRF-Guard", "0")],
    [("X-CSRF-Token", "a"), ("X-CSRF-Token", "b")],
])
def test_duplicate_security_headers_are_rejected(client, headers):
    assert client.post("/api/watch-candidates", json={"symbol":"BTCUSDC"}, headers=headers).status_code == 400


@pytest.mark.parametrize("origin", [ORIGIN + "/path", ORIGIN + "?q=x", ORIGIN + "#x", "null", "http://localhost:8000.evil"])
def test_noncanonical_origins_are_rejected(client, origin):
    assert client.post("/api/watch-candidates", json={"symbol":"BTCUSDC"}, headers={"Origin":origin}).status_code == 403


def test_legacy_scan_requires_csrf(client, monkeypatch):
    client.headers.pop("X-CSRF-Token")
    monkeypatch.setattr(main.scanner, "scan_watchlist", lambda **kw: pytest.fail("Cross-site legacy scan executed"))
    assert client.get("/scan", follow_redirects=False).status_code == 403


@pytest.mark.parametrize("path", ["/api/positions", "/%61pi/positions", "/api//positions", "/api/positions/", "/api/../api/positions"])
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
def test_path_and_method_variants_never_expose_anonymous_data(secured_app, path, method):
    client = TestClient(secured_app(), base_url=ORIGIN, follow_redirects=False)
    response = client.request(method,path)
    assert response.status_code in {401,303}


def test_strict_csp_and_other_headers_are_effective(client):
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    for directive in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'self'", "frame-ancestors 'none'", "form-action 'self'", "base-uri 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert ' style="' not in response.text and "<script>" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert len(response.headers["x-request-id"]) == 32


def test_production_docs_disabled_without_disabling_auth(secured_app):
    app = secured_app(origins=("https://app.example.test",))
    # Rebuild so production settings, not only fixture middleware, take effect.
    from fastapi import FastAPI
    from services.auth import install_security
    production = FastAPI()
    from services.auth import AUTH_PATHS
    production.router.routes.extend(route for route in main.app.routes if getattr(route,"path","") not in AUTH_PATHS)
    install_security(production,main.templates,replace(app.state.auth.settings,production=True))
    client = TestClient(production,base_url="https://app.example.test",follow_redirects=False)
    assert login(client).status_code == 303
    for path in ("/docs","/docs/oauth2-redirect","/redoc","/openapi.json"):
        assert client.get(path).status_code == 404
    assert client.get("/api/auth/session").status_code == 200


@pytest.mark.parametrize("proxies", [("*",),("0.0.0.0/0",),("::/0",),("not-an-ip",)])
def test_proxy_configuration_cannot_trust_every_client(proxies):
    with pytest.raises(ValueError):
        AuthSettings(trusted_proxies=proxies)


def test_environment_defaults_are_local_and_production_requires_tls(monkeypatch):
    monkeypatch.setenv("CRYPTOAI_ENV","production")
    with pytest.raises(ValueError):
        AuthSettings.from_env()
    monkeypatch.setenv("CRYPTOAI_ALLOWED_ORIGINS","https://app.example.test")
    assert AuthSettings.from_env().production is True


@pytest.mark.parametrize("value,expected", [
    ("192.0.2.1","192.0.2.1"),("::ffff:192.0.2.1","192.0.2.1"),
    ("2001:db8:abcd:1234::1","2001:db8:abcd:1234::/64"),
    ("2001:db8:abcd:1234::ffff","2001:db8:abcd:1234::/64"),
    ("invalid","unknown"),
])
def test_ip_budget_normalization(value,expected):
    assert address_key(value) == expected


def test_per_address_limit_cannot_globally_lock_owner(secured_app):
    app = secured_app()
    attacker = TestClient(app,base_url=ORIGIN,client=("192.0.2.10",1234))
    for _ in range(5):
        assert login(attacker,password="wrong").status_code == 401
    response = login(attacker,password="wrong")
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert "Patientez" in response.text
    owner = TestClient(app,base_url=ORIGIN,client=("192.0.2.11",1234))
    assert login(owner).status_code == 303


def test_success_does_not_clear_another_addresses_failures():
    clock = lambda: 1000
    limiter = LoginLimiter(clock)
    for _ in range(5):
        limiter.reserve("192.0.2.1")
    limiter.finish("192.0.2.2",True)
    with pytest.raises(HTTPException) as exc:
        limiter.reserve("192.0.2.1")
    assert exc.value.status_code == 429


def test_limiter_is_atomic_expires_and_stays_bounded():
    now = [1000]
    limiter = LoginLimiter(lambda:now[0])
    def reserve(_):
        try: limiter.reserve("192.0.2.1"); return True
        except HTTPException: return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(reserve,range(30))) == 5
    now[0] = 1060
    assert limiter.reserve("192.0.2.1") == 0
    limiter.max_addresses = 32
    for number in range(1000):
        limiter.reserve(f"10.0.{number//256}.{number%256}")
    assert len(limiter.attempts) == 32
    now[0] = 1120
    limiter.reserve("203.0.113.1")
    assert len(limiter.attempts) == 1


def test_distributed_failures_slow_but_do_not_globally_reject():
    limiter = LoginLimiter(lambda:1000)
    for number in range(50):
        ip = f"192.0.2.{number}"
        limiter.reserve(ip)
        limiter.finish(ip,False)
    assert 0 < limiter.reserve("203.0.113.1") <= 1
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(HTTPException) as exc:
        limiter.acquire()
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "1"
    limiter.release()
    limiter.release()


def test_forged_proxy_headers_do_not_change_untrusted_client_budget(secured_app):
    app = secured_app()
    client = TestClient(app,base_url=ORIGIN,client=("192.0.2.1",1234))
    for number in range(6):
        client.headers["X-Forwarded-For"] = f"198.51.100.{number}"
        client.headers["X-Forwarded-Proto"] = "https"
        response = login(client,password="wrong")
        assert response.status_code == (401 if number < 5 else 429)


@pytest.mark.parametrize("peer,headers,expected_client,expected_scheme",[
    ("192.0.2.9",[(b"x-forwarded-for",b"198.51.100.1"),(b"x-forwarded-proto",b"https")],"192.0.2.9","http"),
    ("127.0.0.1",[(b"x-forwarded-for",b"198.51.100.1"),(b"x-forwarded-proto",b"https")],"198.51.100.1","https"),
    ("127.0.0.1",[(b"x-forwarded-for",b"203.0.113.9, 198.51.100.1, 127.0.0.2")],"198.51.100.1","http"),
])
def test_trusted_proxy_chain_uses_last_untrusted_hop(peer,headers,expected_client,expected_scheme):
    scopes=[]
    async def app(scope,receive,send): scopes.append(scope)
    proxy=TrustedProxyMiddleware(app,networks=("127.0.0.0/8",))
    asyncio.run(proxy({"type":"http","client":(peer,1234),"scheme":"http","headers":headers},None,None))
    assert scopes[0]["client"][0] == expected_client
    assert scopes[0]["scheme"] == expected_scheme


@pytest.mark.parametrize("headers",[
    [(b"x-forwarded-for",b"garbage")],[(b"x-forwarded-for",b"192.0.2.1," )],
    [(b"x-forwarded-proto",b"https,http")],
    [(b"x-forwarded-for",b"192.0.2.1"),(b"x-forwarded-for",b"192.0.2.2")],
])
def test_invalid_forwarding_from_trusted_proxy_fails_closed(headers):
    messages=[]
    async def app(*args): pytest.fail("Malformed forwarding reached application")
    async def send(message): messages.append(message)
    proxy=TrustedProxyMiddleware(app,networks=("127.0.0.1",))
    asyncio.run(proxy({"type":"http","client":("127.0.0.1",1),"scheme":"http","headers":headers},None,send))
    assert messages[0]["status"] == 400


def test_correlated_error_logs_do_not_contain_request_secrets(client,monkeypatch,caplog):
    import logging
    caplog.set_level(logging.INFO,logger="crypto_ai.security")
    marker="secret-password-api-key-cookie-csrf-signature"
    def fail(): raise RuntimeError(marker)
    monkeypatch.setattr(main,"list_positions",fail)
    response=client.get("/api/positions?password="+marker,headers={"X-Request-ID":marker})
    assert response.status_code == 500
    assert marker not in response.text+caplog.text
    records=[json.loads(r.message) for r in caplog.records if r.name=="crypto_ai.security"]
    event=next(r for r in records if r["event"]=="unhandled_exception")
    assert event["route"]=="/api/positions" and event["method"]=="GET"
    assert event["exception_type"]=="RuntimeError" and event["status"]==500
    assert len(event["request_id"])==32 and event["timestamp"]


def test_upstream_failures_never_enter_scan_or_snapshot_payloads(monkeypatch):
    import services.personal_data as personal
    import services.scanner as scanner
    marker="apiKey=secret&signature=very-secret"
    def fail(*args,**kwargs): raise RuntimeError(marker)
    monkeypatch.setattr(scanner,"analyze_symbol_multi",fail)
    monkeypatch.setattr(personal,"resolve_market_symbol",lambda s:{"valid":True,"symbol":s})
    assert marker not in json.dumps(scanner.scan_watchlist_multi(["BTCUSDC"],save_history=False))
    assert marker not in json.dumps(personal.get_market_snapshot("BTCUSDC"))
    assert marker not in json.dumps(personal._build_journal_ai_snapshot("BTCUSDC"))


def test_credentials_repr_excludes_sensitive_values():
    value=Credentials(password="sensitive-password",csrf_token="sensitive-csrf")
    assert "sensitive" not in repr(value)


def test_pending_flood_from_one_address_does_not_evict_another_login(secured_app):
    app=secured_app()
    owner=TestClient(app,base_url=ORIGIN,client=("192.0.2.11",1234))
    page=owner.get("/login")
    import re
    token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    for _ in range(140):
        attacker=TestClient(app,base_url=ORIGIN,client=("192.0.2.12",1234))
        assert attacker.get("/login").status_code==200
    assert len(app.state.auth.sessions)<=9
    assert owner.post("/login",data={"password":TEST_PASSWORD,"csrf_token":token},follow_redirects=False).status_code==303


@pytest.mark.parametrize("ip", ["fe80::1%eth0", "fe80::1%1"])
def test_scoped_ipv6_cannot_bypass_address_budgets(ip):
    assert address_key(ip)=="unknown"


@pytest.mark.parametrize("value", ["::ffff:0.0.0.0/96","0.0.0.0/0"])
def test_ipv4_mapped_proxy_network_cannot_trust_everyone(value):
    with pytest.raises(ValueError):
        AuthSettings(trusted_proxies=(value,))


def test_proxy_networks_normalize_host_bits_and_mapped_ipv4():
    from services.trusted_proxy import trusted_network
    assert str(trusted_network("127.0.0.1/8"))=="127.0.0.0/8"
    assert str(trusted_network("::ffff:127.0.0.1/128"))=="127.0.0.1/32"


def test_legacy_enum_case_is_normalized_without_changing_values():
    assert WatchCreate(symbol="btc",priority="high").priority=="HIGH"
    assert JournalCreate(symbol="btc",emotion="calm",action="attente").action=="ATTENTE"


@pytest.mark.parametrize("body,content_type,status",[
    (b'{"password":"first","password":"second"}',"application/json",400),
    (b'password=x&password=',"application/x-www-form-urlencoded",400),
    (b'{"password":[]}',"application/json",422),
    (b'{"password":null}',"application/json",422),
    (b'{"password":"x","unexpected":true}',"application/json",422),
    (b'x'*5000,"application/json",413),
    (b'{"password":"x"}',"text/plain",415),
], ids=["duplicate-json","duplicate-form","array-password","null-password","unknown-field","oversized","unsupported-type"])
def test_credential_parser_rejects_ambiguous_or_oversized_input(client,body,content_type,status):
    response=client.post("/login",content=body,headers={"Content-Type":content_type})
    assert response.status_code==status
    assert "second" not in response.text


def test_password_verification_concurrency_is_bounded(secured_app,monkeypatch):
    app=secured_app()
    clients=[TestClient(app,base_url=ORIGIN,client=(f"192.0.2.{i+20}",1)) for i in range(3)]
    import re
    tokens=[re.search(r'name="csrf_token" value="([^"]+)"',c.get("/login").text).group(1) for c in clients]
    entered=threading.Event()
    release=threading.Event()
    lock=threading.Lock()
    active=[0]
    peak=[0]
    def verify(password):
        with lock:
            active[0]+=1
            peak[0]=max(peak[0],active[0])
            if active[0]==2: entered.set()
        assert release.wait(timeout=5)
        with lock: active[0]-=1
        return True
    monkeypatch.setattr(app.state.auth,"verify_password",verify)
    def post(i):
        return clients[i].post("/login",data={"password":TEST_PASSWORD,"csrf_token":tokens[i]},follow_redirects=False).status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        first=pool.submit(post,0)
        second=pool.submit(post,1)
        try:
            assert entered.wait(timeout=5)
            assert post(2)==429
        finally:
            release.set()
        assert first.result(timeout=5)==303 and second.result(timeout=5)==303
    assert peak[0]==2 and active[0]==0


@pytest.mark.parametrize("body",[
    b'{"symbol":"BTCUSDC","symbol":"ETHUSDC"}',
    b'{"symbol":"BTCUSDC","note":'+b"["*1100+b"0"+b"]"*1100+b"}",
    b'{"symbol":"BTCUSDC","note":"\xff"}',
])
def test_ambiguous_or_malformed_json_never_mutates(client,body,monkeypatch):
    monkeypatch.setattr(main,"create_watch_candidate",lambda data:pytest.fail("Invalid JSON reached service"))
    assert client.post("/api/watch-candidates",content=body,headers={"Content-Type":"application/json"}).status_code==422


def test_launcher_rejects_accidental_plain_http_lan_binding(monkeypatch):
    from tools import serve
    monkeypatch.setattr("sys.argv",["serve","--host","0.0.0.0"])
    monkeypatch.setattr(serve.uvicorn.Server,"run",lambda *a,**k:pytest.fail("Unsafe listener started"))
    with pytest.raises(SystemExit) as exc:
        serve.main()
    assert exc.value.code==2


@pytest.mark.parametrize("host",["[","[::1","localhost:invalid","user:password@localhost:8000","localhost/path","localhost:8000?x"])
def test_malformed_host_is_a_confidential_rejection(secured_app,host,caplog):
    client=TestClient(secured_app(),base_url=ORIGIN,raise_server_exceptions=False)
    response=client.get("/login",headers={"Host":host})
    assert response.status_code==403
    assert "Traceback" not in caplog.text and "user:password" not in response.text+caplog.text


def test_cancelled_request_does_not_release_an_active_argon_slot(monkeypatch):
    from types import SimpleNamespace
    from services.auth import verify_credential
    service=AuthService(AuthSettings(password_hash=os.environ["CRYPTOAI_AUTH_PASSWORD_HASH"]))
    release=threading.Event()
    entered=threading.Event()
    lock=threading.Lock()
    active=[0]
    def verify(password):
        with lock:
            active[0]+=1
            if active[0]==2: entered.set()
        assert release.wait(timeout=5)
        with lock: active[0]-=1
        return False
    monkeypatch.setattr(service,"verify_password",verify)
    def request(ip):
        return Request({"type":"http","method":"POST","path":"/login","scheme":"http","server":("localhost",8000),
                        "headers":[],"client":(ip,1),"app":SimpleNamespace(state=SimpleNamespace(auth=service))})
    async def exercise():
        tasks=[asyncio.create_task(verify_credential(request(f"192.0.2.{i+1}"),"wrong")) for i in range(2)]
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait,3),timeout=4)
            tasks[0].cancel()
            await asyncio.sleep(0)
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(verify_credential(request("192.0.2.3"),"wrong"),timeout=1)
            assert exc.value.status_code==429
        finally:
            release.set()
            await asyncio.gather(*tasks,return_exceptions=True)
    asyncio.run(exercise())
    assert active[0]==0


def test_ipv4_mapped_transport_peer_obeys_explicit_proxy_trust():
    scopes=[]
    async def app(scope,receive,send): scopes.append(scope)
    proxy=TrustedProxyMiddleware(app,networks=("127.0.0.1/32",))
    asyncio.run(proxy({"type":"http","client":("::ffff:127.0.0.1",1),"scheme":"http",
                      "headers":[(b"x-forwarded-for",b"192.0.2.1")]},None,None))
    assert scopes[0]["client"][0]=="192.0.2.1"


def test_pre_route_csrf_logs_template_without_query_secrets(client,caplog):
    import logging
    caplog.set_level(logging.INFO,logger="crypto_ai.security")
    marker="sensitive-query-marker"
    response=client.post("/api/positions?password="+marker,json={},headers={"X-CSRF-Token":"wrong"})
    assert response.status_code==403
    events=[json.loads(record.message) for record in caplog.records if record.name=="crypto_ai.security"]
    event=next(value for value in events if value["event"]=="csrf_rejected")
    assert event["route"]=="/api/positions" and event["method"]=="POST"
    assert marker not in caplog.text
