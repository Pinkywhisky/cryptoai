"""Isolated real-HTTP fixture. Never used by the production launcher."""

import atexit
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs

root = Path(__file__).resolve().parents[1]
isolation = Path(os.environ["CRYPTOAI_TEST_ROOT"]).resolve()
if not isolation.is_relative_to("/tmp") or not isolation.name.startswith("cryptoai-http-"):
    raise RuntimeError("An isolated temporary directory is required")
os.environ.update({
    "CRYPTOAI_DB_PATH": str(isolation / "history.db"),
    "PYTHON_DOTENV_DISABLED": "1", "BINANCE_API_KEY": "", "BINANCE_API_SECRET": "",
    "CRYPTOAI_ALLOW_DESTRUCTIVE": "0",
})
counts = {"network_attempts": 0, "personal_accesses": 0, "sqlite_outside_tmp": 0}
PERSONAL_FILES = {(root / ".env").resolve(), (root / "database/history.db").resolve()}


def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg"}:
        counts["network_attempts"] += 1
        raise AssertionError("Outbound network forbidden in HTTP fixture")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)) and Path(os.fsdecode(args[0])).resolve() in PERSONAL_FILES:
        counts["personal_accesses"] += 1
        raise AssertionError("Personal file forbidden in HTTP fixture")
    if event == "sqlite3.connect":
        name = os.fsdecode(args[0])
        if name.startswith("file:"):
            parsed=urlsplit(name)
            if parse_qs(parsed.query).get("mode")==["memory"]:
                return
            name=unquote(parsed.path)
        if name not in ("", ":memory:") and not Path(name).resolve().is_relative_to(isolation):
            counts["sqlite_outside_tmp"] += 1
            raise AssertionError("SQLite must stay in fixture directory")


sys.addaudithook(audit)
atexit.register(lambda: print("ISOLATION_AUDIT=" + json.dumps(counts), flush=True))
sys.path.insert(0, str(root))
os.chdir(root)
logging.basicConfig(level=logging.INFO, format="%(message)s")

import main
import services.personal_data as personal

payload = '<img src=x onerror="globalThis.compromised=1"> & Unicode \u00e9'
row = {
    "symbol": payload, "current_price": 10, "global_score": 55, "global_trend": "NEUTRAL",
    "setup_quality": payload, "sources": ["OPPORTUNITY_SCANNER"],
    "opportunity_info": {"reason": payload, "scan_score": 70},
    "decision_engine": {
        "decision": "WAIT", "decision_label": payload, "confidence": 55,
        "reason_summary": payload, "blocking_factors": [payload], "positive_factors": [payload],
        "timing_label": payload, "timing_action": payload, "timing_reason": payload,
    },
}
main._build_market_payload = lambda: {"results": [row], "universe": [], "counts": {"total": 1}}
main.generate_ai_alerts = lambda *args, **kwargs: [
    {"symbol": payload, "type": "RISK", "severity": "HIGH", "title": payload, "message": payload, "confidence": 50}
]
main.get_pnl_summary = lambda: {"realized_pnl": 0, "usdc_available": 0}
main.sync_binance_account = lambda: {"synced": True, "read_only_mode": True}
personal.resolve_market_symbol = lambda symbol: {
    "valid": True, "symbol": symbol.upper().replace("/", ""), "resolved_symbol": symbol.upper().replace("/", ""),
    "base_asset": symbol, "market_source": "BINANCE_SPOT",
}
personal.get_market_snapshot = lambda symbol: {
    "symbol": symbol, "analysis_available": False, "is_valid_symbol": True,
    "current_price": 10, "market_source": "BINANCE_SPOT",
}
personal._build_journal_ai_snapshot = lambda symbol: {
    "market": {"current_price": 10}, "decision": {"reason_summary": payload},
    "resolution": {"market_source": "BINANCE_SPOT"}, "market_regime": {},
}
from contextlib import asynccontextmanager
original_lifespan = main.app.router.lifespan_context


@asynccontextmanager
async def audited_lifespan(app):
    async with original_lifespan(app) as state:
        try:
            yield state
        finally:
            print("ISOLATION_AUDIT=" + json.dumps(counts), flush=True)


main.app.router.lifespan_context = audited_lifespan
original_clock = main.app.state.auth.clock
offset = [0]
main.app.state.auth.clock = lambda: original_clock() + offset[0]


@main.app.post("/api/test/expire", include_in_schema=False)
def expire():
    offset[0] += 90000
    return {"expired": True}


@main.app.get("/api/test/error", include_in_schema=False)
def error():
    raise RuntimeError("secret-password-api-key-cookie-csrf-signature")


@main.app.get("/api/test/reflected", include_in_schema=False)
def reflected():
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": payload}, status_code=400)


@main.app.get("/api/test/stream-error", include_in_schema=False)
def stream_error():
    from starlette.responses import StreamingResponse
    def chunks():
        yield b"partial"
        raise RuntimeError("secret-password-api-key-cookie-csrf-signature")
    return StreamingResponse(chunks(), headers={"Content-Length":"100"})


if __name__ == "__main__":
    import uvicorn
    import socket
    listener = socket.socket(fileno=int(os.environ["CRYPTOAI_TEST_FD"]))
    if os.environ.get("CRYPTOAI_TEST_LAUNCHER") == "1":
        from tools.serve import main as serve
        sys.argv = ["tools/serve.py", "--port", os.environ["CRYPTOAI_TEST_PORT"]]
        serve(sockets=[listener])
        sys.exit(0)
    config = uvicorn.Config(main.app, host="127.0.0.1", port=int(os.environ["CRYPTOAI_TEST_PORT"]),
                workers=1, proxy_headers=False, access_log=False, limit_concurrency=64,
                timeout_keep_alive=5, log_level="warning")
    uvicorn.Server(config).run(sockets=[listener])
