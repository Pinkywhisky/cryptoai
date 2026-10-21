"""Real Uvicorn probes against temporary SQLite; outbound traffic is loopback-only.

Run: venv/bin/python -B tools/verify_security_http.py
Optional browser fixture: append --serve-browser, then create the printed stop file.
"""

import argparse
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# Do not import main in this process.
import httpx
from pwdlib import PasswordHash

PASSWORD = "test-only-cryptoai-http-password-2026"
HASH = PasswordHash.recommended().hash(PASSWORD)
COUNTS = {"external_network_attempts": 0, "personal_accesses": 0, "sqlite_outside_tmp": 0}
PERSONAL = {(ROOT / ".env").resolve(), (ROOT / "database/history.db").resolve()}


def audit(event, args):
    if event == "socket.connect":
        address = args[1]
        try:
            allowed = ipaddress.ip_address(address[0]).is_loopback
        except (ValueError, TypeError, IndexError):
            allowed = False
        if not allowed:
            COUNTS["external_network_attempts"] += 1
            raise AssertionError("External network forbidden")
    if event == "socket.getaddrinfo":
        try:
            allowed = ipaddress.ip_address(os.fsdecode(args[0])).is_loopback
        except ValueError:
            allowed = os.fsdecode(args[0]) == "localhost"
        if not allowed:
            COUNTS["external_network_attempts"] += 1
            raise AssertionError("External DNS forbidden")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)) and Path(os.fsdecode(args[0])).resolve() in PERSONAL:
        COUNTS["personal_accesses"] += 1
        raise AssertionError("Personal file forbidden")
    if event == "sqlite3.connect":
        name = os.fsdecode(args[0])
        if name not in ("", ":memory:") and not Path(name).resolve().is_relative_to("/tmp"):
            COUNTS["sqlite_outside_tmp"] += 1
            raise AssertionError("Only temporary SQLite allowed")


sys.addaudithook(audit)


class Server:
    def __init__(self, *, proxy=False, untrusted_https=False, launcher=False):
        self.directory = tempfile.TemporaryDirectory(prefix="cryptoai-http-")
        self.root = Path(self.directory.name)
        self.proxy = proxy
        self.launcher = launcher
        self.untrusted_https = untrusted_https
        self.logs = []
        self.process = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.log_path = self.root / "server.log"

    def start(self):
        env = {key:value for key,value in os.environ.items()
               if not key.startswith(("CRYPTOAI_", "BINANCE_")) and key not in {"PYTHONPATH"}}
        env.update({
            "CRYPTOAI_TEST_FD": str(self.listener.fileno()),
            "CRYPTOAI_TEST_ROOT": str(self.root), "CRYPTOAI_TEST_PORT": str(self.port),
            "CRYPTOAI_AUTH_PASSWORD_HASH": HASH,
            "CRYPTOAI_ALLOWED_ORIGINS": "https://app.example.test" if self.proxy or self.untrusted_https else self.url,
            "CRYPTOAI_TRUSTED_PROXIES": "127.0.0.1/32" if self.proxy else "",
            "CRYPTOAI_TEST_LAUNCHER": "1" if self.launcher else "0",
            "CRYPTOAI_ENV": "production" if self.proxy or self.untrusted_https else "development",
            "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.log_file = self.log_path.open("w")
        self.process = subprocess.Popen([sys.executable, "-B", str(ROOT/"tests/http_fixture.py")],
                                       cwd=ROOT, env=env, stdout=self.log_file, stderr=subprocess.STDOUT, pass_fds=(self.listener.fileno(),))
        with self.client() as client:
            for _ in range(150):
                try:
                    response = client.get("/login")
                    if response.status_code in {200,403}:
                        return self
                except httpx.TransportError:
                    pass
                if self.process.poll() is not None:
                    raise RuntimeError(self.log_path.read_text())
                time.sleep(0.02)
        raise RuntimeError("Fixture did not start")

    def client(self, *, address="127.0.0.1"):
        headers = {"Host":"app.example.test", "X-Forwarded-Proto":"https", "X-Forwarded-For":"198.51.100.10"} if self.proxy or self.untrusted_https else {}
        return httpx.Client(base_url=self.url, headers=headers, follow_redirects=False, timeout=5,
                            transport=httpx.HTTPTransport(local_address=address), trust_env=False)

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.log_file.close()
            log = self.log_path.read_text()
            assert "secret-password-api-key-cookie-csrf-signature" not in log, "Secret leaked in real server logs"
            assert "Traceback" not in log, "Unexpected traceback in fixture logs"
            if self.launcher:
                assert '"event": "credential_accepted"' in log, "Supported launcher must emit security events"
            audits = re.findall(r"ISOLATION_AUDIT=(\{[^\n]+\})", log)
            assert audits and all(not any(json.loads(value).values()) for value in audits), "Fixture isolation audit failed"
            self.logs.append(log)
            self.process = None

    def close(self):
        try:
            self.stop()
        finally:
            self.listener.close()
            self.directory.cleanup()

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            if self.process is not None:
                self.process.terminate()
                self.process.wait(timeout=8)
                self.log_file.close()
            self.listener.close()
            self.directory.cleanup()
            raise

    def __exit__(self,*args):
        self.close()


def token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def authenticate(client,password=PASSWORD,proxy=False):
    page=client.get("/login")
    assert page.status_code==200
    # A real reverse proxy forwards the cookie received over client HTTPS.
    # httpx's connection to this fixture is intentionally plaintext loopback.
    current = page.headers.get("set-cookie")
    if current:
        current = current.split(";",1)[0]
    else:
        current = client.headers.get("Cookie") or "cryptoai_session=" + client.cookies.get("cryptoai_session")
    if proxy:
        client.headers["Cookie"]=current
    before=current
    response=client.post("/login",data={"password":password,"csrf_token":token(page)})
    if response.status_code==303:
        if proxy:
            client.headers["Cookie"]=response.headers["set-cookie"].split(";",1)[0]
        client.headers["X-CSRF-Guard"]="1"
    return response,before


def probes():
    checks=[]
    def check(name,condition):
        assert condition,name
        checks.append(name)
    with Server() as server:
        with server.client() as client:
            check("anonymous sensitive API",client.get("/api/positions").status_code==401)
            page=client.get("/login")
            check("login CSRF",client.post("/login",data={"password":PASSWORD}).status_code==403)
            response,old_cookie=authenticate(client)
            check("real login",response.status_code==303)
            check("HttpOnly SameSite cookie","httponly" in response.headers["set-cookie"].lower() and "samesite=strict" in response.headers["set-cookie"].lower())
            check("cockpit and CSP",client.get("/").status_code==200 and "script-src 'self'" in client.get("/").headers["content-security-policy"])
            with server.client() as replay:
                replay.headers["Cookie"]=old_cookie
                check("pre-login cookie revoked",replay.get("/api/positions").status_code==401)
            hostile='<img src=x onerror="globalThis.compromised=1">'
            position=client.post("/api/positions",json={"symbol":"BTCUSDC","quantity":2,"average_buy_price":10,"invested_amount":20})
            check("position creation",position.status_code==200)
            pid=position.json()["id"]
            check("position partial update",client.put(f"/api/positions/{pid}",json={"note":hostile}).json()["note"]==hostile)
            before=client.get("/api/positions").json()
            check("invalid position rejected atomically",client.put(f"/api/positions/{pid}",json={"quantity":-1,"note":"lost"}).status_code==422 and client.get("/api/positions").json()==before)
            watch=client.post("/api/watch-candidates",json={"symbol":"ETHUSDC","note":hostile})
            check("watch creation",watch.status_code==200)
            wid=watch.json()["id"]
            check("watch delete",client.delete(f"/api/watch-candidates/{wid}").status_code==200)
            journal=client.post("/api/journal",json={"symbol":"BTCUSDC","reason":hostile})
            check("stored hostile journal",journal.status_code==200 and client.get("/api/journal").json()[0]["reason"]==hostile)
            check("journal result",client.put(f"/api/journal/{journal.json()['id']}",json={"result_status":"WIN","pnl_percent":2}).status_code==200)
            check("sync simulated only",client.post("/api/binance/sync").json()["synced"] is True)
            check("destructive default disabled",client.post("/api/admin/reset",json={}).status_code==403)
            check("cross-origin blocked",client.post("/api/watch-candidates",json={"symbol":"BTCUSDC"},headers={"Origin":"https://evil.example"}).status_code==403)
            check("CORS preflight denied","access-control-allow-origin" not in client.options("/api/positions",headers={"Origin":"https://evil.example"}).headers)
            check("real 500 confidential",client.get("/api/test/error").status_code==500)
            try:
                client.get("/api/test/stream-error")
            except httpx.RemoteProtocolError:
                check("late streaming failure confidential",True)
            else:
                check("late streaming failure confidential",False)
            check("oversized body",client.put("/api/positions/1",content=b"x"*70000).status_code==413)
            client.headers.pop("X-CSRF-Guard")
            check("legacy scan CSRF",client.get("/scan").status_code==403)
            client.headers["X-CSRF-Guard"]="1"
            cookie=client.cookies.get("cryptoai_session")
            csrf=token(client.get("/"))
            check("logout",client.post("/logout",data={"csrf_token":csrf}).status_code==303)
            with server.client() as replay:
                replay.cookies.set("cryptoai_session",cookie)
                check("logout replay denied",replay.get("/api/positions").status_code==401)
            assert authenticate(client)[0].status_code==303
            check("expiry trigger",client.post("/api/test/expire").status_code==200)
            check("absolute session expiry",client.get("/api/positions").status_code==401)
            assert authenticate(client)[0].status_code==303
            cookie=client.cookies.get("cryptoai_session")
        server.stop()
        server.start()
        with server.client() as client:
            client.cookies.set("cryptoai_session",cookie)
            check("restart invalidates sessions",client.get("/api/positions").status_code==401)
    with Server() as server:
        with server.client(address="127.0.0.2") as attacker:
            for number in range(6):
                attacker.headers["X-Forwarded-For"]=f"192.0.2.{number}"
                attacker.headers["X-Forwarded-Proto"]="https"
                response,_=authenticate(attacker,password="wrong")
                check(f"untrusted forwarding budget {number+1}",response.status_code==(401 if number<5 else 429))
        with server.client() as owner:
            check("owner after attack",authenticate(owner)[0].status_code==303)
        def burst(number):
            with server.client(address="127.0.0.3") as attacker:
                return authenticate(attacker,password="wrong")[0].status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses=list(pool.map(burst,range(12)))
        check("concurrent login bounded",all(status in {401,403,429} for status in statuses) and 429 in statuses)
        with server.client() as owner:
            check("owner after concurrent attack",authenticate(owner)[0].status_code==303)
    with Server(untrusted_https=True) as server:
        with server.client() as client:
            check("untrusted proto cannot enable HTTPS origin",client.get("/login").status_code==403)
    with Server(proxy=True) as server:
        with server.client() as client:
            response,_=authenticate(client,proxy=True)
            check("explicit proxy HTTPS login",response.status_code==303)
            check("Secure cookie behind proxy","; secure" in response.headers["set-cookie"].lower())
            check("HSTS behind proxy",response.headers["strict-transport-security"]=="max-age=31536000")
            check("proxy authenticated API",client.get("/api/positions").status_code==200)
            for path in ("/docs","/redoc","/openapi.json"):
                check("production disabled "+path,client.get(path).status_code==404)
            response=client.get("/login",headers={"X-Forwarded-For":"not-an-ip"})
            check("malformed trusted forwarding",response.status_code==400)
    with Server(launcher=True) as server:
        with server.client() as client:
            check("supported launcher actual login",authenticate(client)[0].status_code==303)
            check("supported launcher ignores spoofed proto",client.get("/api/positions",headers={"X-Forwarded-Proto":"https"}).status_code==200)
    check("probe isolation",not any(COUNTS.values()))
    return checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-browser",action="store_true")
    args=parser.parse_args()
    if args.serve_browser:
        with Server() as server:
            print(json.dumps({"url":server.url,"stop_file":str(server.root/"stop"),"test_password":PASSWORD}),flush=True)
            deadline=time.monotonic()+600
            while not (server.root/"stop").exists() and time.monotonic()<deadline:
                time.sleep(0.1)
    else:
        checks=probes()
        print(json.dumps({"checks_passed":len(checks),"checks":checks,"isolation":COUNTS},ensure_ascii=False))


if __name__=="__main__":
    main()
