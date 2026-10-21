"""Run pytest with audited network/personal-file guards and optional stdlib line coverage."""

import argparse
import json
import os
import socket
import sys
import tempfile
from urllib.parse import urlsplit, unquote, parse_qs
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"
COUNTS={"network_attempts":0,"personal_accesses":0,"sqlite_outside_tmp":0}
PERSONAL={(ROOT/".env").resolve(),(ROOT/"database/history.db").resolve()}


def audit(event,args):
    if event in {"socket.connect","socket.getaddrinfo","socket.sendto","socket.sendmsg"}:
        COUNTS["network_attempts"]+=1
        raise AssertionError("Network forbidden by audit")
    if event=="open" and isinstance(args[0],(str,bytes,os.PathLike)) and Path(os.fsdecode(args[0])).resolve() in PERSONAL:
        COUNTS["personal_accesses"]+=1
        raise AssertionError("Personal file forbidden")
    if event=="sqlite3.connect":
        name=os.fsdecode(args[0])
        if name.startswith("file:"):
            parsed=urlsplit(name)
            if parse_qs(parsed.query).get("mode")==["memory"]:
                return
            name=unquote(parsed.path)
        if name not in ("",":memory:") and not Path(name).resolve().is_relative_to("/tmp"):
            COUNTS["sqlite_outside_tmp"]+=1
            raise AssertionError("Only temporary SQLite allowed")


sys.addaudithook(audit)
import pytest


class OfflineAudit:
    @pytest.hookimpl(trylast=True)
    def pytest_configure(self,config):
        def reject(*args,**kwargs):
            COUNTS["network_attempts"]+=1
            raise AssertionError("Network forbidden")
        socket.getaddrinfo=reject
        for name in ("connect","connect_ex","sendto","sendmsg"):
            if hasattr(socket.socket,name):
                setattr(socket.socket,name,reject)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage",action="store_true")
    args, selectors=parser.parse_known_args()
    def run():
        return pytest.main(["-q","-p","no:cacheprovider","--tb=short",*selectors],plugins=[OfflineAudit()])
    if args.coverage:
        import threading
        import trace
        tracer=trace.Trace(count=True,trace=False,ignoredirs=[sys.base_prefix,str(ROOT/"venv")])
        # trace._Ignore caches by basename: requests/auth.py or pytest/main.py
        # would otherwise hide application auth.py/main.py. Cache by full path.
        decisions = {}
        def ignore_file(filename, module_name):
            if filename not in decisions:
                target = Path(filename).resolve()
                decisions[filename] = not target.is_relative_to(ROOT) or target.is_relative_to(ROOT/"venv")
            return decisions[filename]
        tracer.ignore.names = ignore_file
        threading.settrace(tracer.globaltrace)
        try:
            code=tracer.runfunc(run)
        finally:
            threading.settrace(None)
        results=tracer.results()
        # Jinja compiles templates into Python frames with .html filenames.
        # Report application Python lines only; DOM/templates are tested separately.
        results.counts = {(str(Path(key[0]).resolve()),key[1]):count for key,count in results.counts.items()
                          if key[0].endswith(".py") and Path(key[0]).resolve().is_relative_to(ROOT)
                          and not Path(key[0]).resolve().is_relative_to(ROOT/"venv")}
        directory=Path(tempfile.mkdtemp(prefix="cryptoai-coverage-"))
        results.write_results(show_missing=True,summary=False,coverdir=str(directory))
        targets=["main.py","services/auth.py","services/login_limiter.py","services/api_models.py",
                 "services/trusted_proxy.py","services/security_logging.py",
                 "services/binance_private_client.py","services/binance_trade_history.py",
                 "services/binance_portfolio.py","services/binance_sync.py","services/pnl.py",
                 "services/financial_migrations.py","services/history.py",
                 "services/personal_data.py","services/market_universe.py"]
        summary={}
        for name in targets:
            filename=str(ROOT/name)
            executable=trace._find_executable_linenos(filename)
            hits={line for (file,line),count in results.counts.items() if file==filename and count}
            total=len(executable)
            covered=len(set(executable)&hits)
            summary[name]={"covered":covered,"executable":total,"percent":round(100*covered/total,1) if total else 100}
        (directory/"summary.json").write_text(json.dumps(summary,indent=2))
        print("STDLIB_LINE_COVERAGE="+json.dumps({"directory":str(directory),"modules":summary}))
    else:
        code=run()
    print("ISOLATION_AUDIT="+json.dumps(COUNTS))
    return code or (1 if any(COUNTS.values()) else 0)


if __name__=="__main__":
    sys.exit(main())
