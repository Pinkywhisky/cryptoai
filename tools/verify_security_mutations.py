"""Reintroduce security defects ONLY in disposable source copies; detect them with real tests."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# (name, file, exact text, replacement, selectors)
MUTATIONS=[
 ("global_login_lockout","services/login_limiter.py",'key = address_key(address)','key = "global"',
  ["tests/test_hardening.py::test_per_address_limit_cannot_globally_lock_owner"]),
 ("csrf_bypass","services/auth.py","if not record or not (valid_token or ajax_guard):","if False:",
  ["tests/test_security.py::test_every_business_mutation_requires_csrf"]),
 ("loose_csp","services/auth.py",'''script-src 'self'; script-src-attr 'none';''',"""script-src 'self' 'unsafe-inline'; script-src-attr 'unsafe-inline';""",
  ["tests/test_hardening.py::test_strict_csp_and_other_headers_are_effective"]),
 ("accept_unknown_fields","services/api_models.py",'extra="forbid"','extra="ignore"',
  ["tests/test_hardening.py::test_invalid_write_is_422_without_any_persistence"]),
 ("allow_nonfinite","services/api_models.py",'strict=True, allow_inf_nan=False','strict=True, allow_inf_nan=True',
  ["tests/test_hardening.py::test_nonfinite_json_never_reaches_storage"]),
 ("allow_negative_quantity","services/api_models.py",'Positive = Annotated[Number, Field(gt=0)]','Positive = Number',
  ["tests/test_hardening.py::test_invalid_write_is_422_without_any_persistence"]),
 ("trust_spoofed_proxy","services/trusted_proxy.py",'if peer_address is not None and self.trusted(peer_address):','if peer_address is not None:',
  ["tests/test_hardening.py::test_forged_proxy_headers_do_not_change_untrusted_client_budget"]),
 ("destructive_enabled_by_default","services/auth.py",'if not service.settings.allow_destructive:','if False:',
  ["tests/test_security.py::test_destructive_actions_require_flag_csrf_confirmation_and_password"]),
 ("cookie_not_revoked","services/auth.py",'if isinstance(sid, str):\n                self.sessions.pop(sid, None)','if isinstance(sid, str):\n                pass',
  ["tests/test_security.py::test_logout_requires_csrf_and_revokes_replayed_cookie"]),
 ("raw_upstream_exception","services/personal_data.py",'"market_error": "Analyse de marche indisponible"','"market_error": str(exc)',
  ["tests/test_hardening.py::test_upstream_failures_never_enter_scan_or_snapshot_payloads"]),
 ("unbounded_request","services/auth.py",'if size > 65536:','if size > 1048576:',
  ["tests/test_hardening.py::test_request_limits_apply_to_declared_and_streamed_bodies"]),
]


def sources():
    names=set()
    for args in (["git","ls-files","-z"],["git","ls-files","--others","--exclude-standard","-z"]):
        names.update(name for name in subprocess.check_output(args,cwd=ROOT,text=True).split(chr(0)) if name)
    for name in sorted(names):
        path=Path(name)
        assert not path.is_absolute() and ".." not in path.parts
        if path.parts[0] in {".git","venv","database"} or path.name.startswith(".env") or path.suffix in {".db",".sqlite",".sqlite3",".bak",".patch"}:
            continue
        yield name


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node",help="Optional Node executable path for the XSS mutation")
    args=parser.parse_args()
    files=list(sources())
    summaries=[]
    for name,file,old,new,selectors in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="cryptoai-mutation-") as directory:
            copy=Path(directory)
            for relative in files:
                target=copy/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/relative,target)
            path=copy/file
            text=path.read_text()
            assert old in text,name
            path.write_text(text.replace("65536","1048576") if name=="unbounded_request" else text.replace(old,new,1))
            run=subprocess.run([sys.executable,"-B",str(copy/"tools/verify_offline.py"),*selectors],
                               cwd=copy,capture_output=True,text=True,timeout=40)
            killed=run.returncode==1 and re.search(r"\b[1-9][0-9]* failed\b",run.stdout) is not None and "ERROR collecting" not in run.stdout
            audits=re.findall(r"ISOLATION_AUDIT=(\{[^\n]+\})",run.stdout)
            isolated=bool(audits) and not any(json.loads(audits[-1]).values())
            result={"mutation":name,"detected":killed,"isolation_ok":isolated}
            summaries.append(result)
            if not killed or not isolated:
                print(run.stdout[-7000:]+run.stderr[-2000:])
    if args.node:
        with tempfile.TemporaryDirectory(prefix="cryptoai-mutation-") as directory:
            copy=Path(directory)
            for relative in ("static/app.js","tests/test_cockpit.js"):
                target=copy/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/relative,target)
            path=copy/"static/app.js"
            text=path.read_text()
            old="${escapeHtml(item.symbol)}"
            assert old in text
            path.write_text(text.replace(old,"${item.symbol}",1))
            node_test=str(copy/"tests/test_cockpit.js")
            if args.node.endswith(".exe") and os.name!="nt":
                node_test="\\\\wsl.localhost\\Debian"+node_test.replace("/","\\")
            run=subprocess.run([args.node,node_test],capture_output=True,text=True,timeout=20)
            killed=run.returncode==1 and re.search(r"(?:fail|failing) [1-9]",run.stdout) is not None
            summaries.append({"mutation":"unescaped_market_symbol","detected":killed,"isolation_ok":"NETWORK_ATTEMPTS=0" in run.stdout})
            if not killed:
                print(run.stdout[-3000:]+run.stderr[-1000:])
    print("MUTATION_RESULTS="+json.dumps({"detected":sum(item["detected"] for item in summaries),"total":len(summaries),"results":summaries}))
    return 0 if all(item["detected"] and item["isolation_ok"] for item in summaries) else 1


if __name__=="__main__":
    sys.exit(main())
