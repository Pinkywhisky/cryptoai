"""Reproducible Alpha before/after measurements: fake HTTP and temporary DB only.

Run: venv/bin/python -B tools/measure_alpha.py --output /tmp/alpha-measures.json
No personal data, live Binance transport, credentials or synchronization is used.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "379c4f66a1c2573fbe64438809927e5f42806965"


def worker(root, path, count, scenario):
    os.chdir(root)
    sys.path.insert(0, str(root))
    for key in list(os.environ):
        if key.startswith("CRYPTOAI_API_"):
            os.environ.pop(key)
    os.environ.update(CRYPTOAI_DB_PATH=str(path), PYTHON_DOTENV_DISABLED="1",
                      BINANCE_API_KEY="", BINANCE_API_SECRET="")
    audit_counts = {"network_attempts": 0, "personal_accesses": 0, "sqlite_outside_tmp": 0}
    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg"}:
            audit_counts["network_attempts"] += 1
            raise AssertionError("Real network forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            candidate = Path(os.fsdecode(args[0])).resolve()
            if candidate.name == ".env" or candidate == (ROOT/"database/history.db").resolve():
                audit_counts["personal_accesses"] += 1
                raise AssertionError("Personal data forbidden")
        if event == "sqlite3.connect" and args[0] != ":memory:":
            name = str(args[0])
            if name.startswith("file:"):
                name = unquote(urlsplit(name).path)
            if not Path(name).resolve().is_relative_to(path.parent):
                audit_counts["sqlite_outside_tmp"] += 1
                raise AssertionError("Only this temporary DB allowed")
    sys.addaudithook(audit)
    import requests
    from services import binance_alpha as alpha, symbols, binance_api_budget as quotas

    guard = threading.RLock()
    now = [1800000000.]
    def clock():
        with guard:
            return now[0]
    def advance(seconds):
        with guard:
            now[0] += seconds
    # Old Alpha uses time.time; new Alpha deliberately injects its own clock.
    alpha.time.time = clock
    alpha._clock = clock
    alpha._sleep = advance
    budget = quotas.ApiBudget(path, clock=clock)
    quotas.ApiBudget = lambda: budget
    alpha.ApiBudget = lambda: budget
    symbols.get_exchange_symbols = lambda force=False: {"BTCUSDC"}
    calls, rejected, reasons = [], [0], {}
    original_reserve = budget.reserve
    def reserve(*args, **kwargs):
        try:
            return original_reserve(*args, **kwargs)
        except quotas.ApiQuotaError as error:
            with guard:
                rejected[0] += 1
                reasons[error.code] = reasons.get(error.code, 0)+1
            raise
    budget.reserve = reserve
    failed = [False]
    class Response:
        status_code = 200
        headers = {}
        def __init__(self, data):
            self.data = {"code": "000000", "success": True, "data": data}
        def json(self):
            return self.data
        def raise_for_status(self):
            pass
    def fake_get(url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        kind = ("spot" if "/api/v3/" in url else "catalog" if "token/list" in url
                else "markets" if "get-exchange-info" in url
                else "candles" if "klines" in url else "ticker")
        with guard:
            calls.append(kind)
        time.sleep(.001)  # Measured synthetic latency; never a real HTTP request.
        if failed[0] and kind != "spot":
            raise requests.Timeout("simulated")
        if kind == "spot":
            return Response({})
        if kind == "catalog":
            return Response([{"symbol": "TOK"+str(i), "alphaId": "ALPHA_"+str(i)}
                             for i in range(count)])
        if kind == "markets":
            return Response({"symbols": [{"baseAsset": f"ALPHA_{i}", "quoteAsset": "USDT",
                "symbol": f"ALPHA_{i}USDT", "status": "TRADING"} for i in range(count)]})
        if kind == "ticker":
            return Response({"lastPrice": "3", "priceChangePercent": "5",
                             "closeTime": int(clock()*1000)})
        ms = int(clock()*1000)
        return Response([[ms-(120-i)*3600000, "2", "5", "1", str(2+i/100), "100",
                          ms-(119-i)*3600000-1, "300", "5", 0, 0, 0] for i in range(120)])
    requests.get = fake_get
    def refresh():
        outcomes = []
        for index in range(count):
            symbol = "TOK"+str(index)
            result = symbols.resolve_market_symbol(symbol)
            if result.get("valid"):
                result = alpha.get_alpha_market_data(symbol)
            outcomes.append(result)
        return outcomes
    phases = []
    for phase in ("cold", "warm", "expired"):
        if phase == "expired":
            advance(601)
            failed[0] = scenario == "outage"
        before_calls, before_rejections, before_clock = len(calls), rejected[0], clock()
        started = time.perf_counter()
        if scenario == "sync" and phase == "cold":
            def simulate_sync():
                for _ in range(20):
                    quotas.budgeted_get("https://api.binance.com/api/v3/account", _budget=budget)
            with ThreadPoolExecutor(max_workers=2) as pool:
                sync = pool.submit(simulate_sync)
                rows = refresh()
                sync.result()
        else:
            rows = refresh()
        elapsed = time.perf_counter()-started
        phases.append({"phase": phase, "alpha_http_calls": sum(c != "spot" for c in calls[before_calls:]),
            "spot_http_calls": sum(c == "spot" for c in calls[before_calls:]),
            "prices_available": sum(r.get("current_price") is not None for r in rows),
            "analyses_available": sum(bool(r.get("candles_available")) for r in rows),
            "classified_invalid": sum(r.get("valid") is False for r in rows),
            "unresolved_temporarily": sum(r.get("valid") is None for r in rows),
            "budget_deferrals": rejected[0]-before_rejections,
            "final_price_unavailable": sum(r.get("current_price") is None for r in rows),
            "measured_wall_ms": round(elapsed*1000, 3),
            "simulated_wait_seconds": round(clock()-before_clock, 3)})
    metrics = budget.metrics("ALPHA")
    result = {"tokens": count, "scenario": scenario, "phases": phases,
              "consumption": metrics["weight"],
              "accounting_unit": metrics.get("accounting_unit", "OLD_ESTIMATED_WEIGHT"),
              "spot_consumption": budget.metrics("SPOT")["weight"],
              "budget_deferral_reasons_total": reasons,
              "cache": alpha.alpha_cache_metrics() if hasattr(alpha, "alpha_cache_metrics") else None,
              "isolation": audit_counts}
    if any(audit_counts.values()):
        raise AssertionError("Isolation violation")
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", nargs=4, metavar=("ROOT", "DB", "COUNT", "SCENARIO"))
    args = parser.parse_args()
    if args.worker:
        root, path, count, scenario = args.worker
        worker(Path(root), Path(path), int(count), scenario)
        return
    with tempfile.TemporaryDirectory(prefix="cryptoai-alpha-measure-") as directory:
        temp = Path(directory)
        before = temp/"before"
        before.mkdir()
        archive = subprocess.check_output(["git", "archive", "--format=tar", REFERENCE], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(before, filter="data")
        results = {"reference": REFERENCE, "latency": "simulated 1ms/HTTP; wall time measured locally",
                   "before": [], "after": []}
        for label, source in (("before", before), ("after", ROOT)):
            for count, scenario in [(n, "normal") for n in (1, 2, 3, 6, 10)]+[(10, "sync"), (10, "outage")]:
                db = temp/(label+str(count)+scenario+".db")
                command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                           str(source), str(db), str(count), scenario]
                run = subprocess.run(command, capture_output=True, text=True)
                if run.returncode:
                    raise RuntimeError("Isolated measurement failed: "+run.stderr[-4000:])
                results[label].append(json.loads(run.stdout))
        serialized = json.dumps(results, indent=2)
        if args.output:
            args.output.write_text(serialized+"\n")
        print(serialized)


if __name__ == "__main__":
    main()
