"""Targeted financial mutations in disposable copies only; no original source mutation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.verify_security_mutations import sources

MUTATIONS = json.loads('[["unknown_cost_is_zero", "services/pnl.py", "invested = known_cost if cost_complete else None", "invested = known_cost", ["tests/test_financial_reference.py::test_missing_cost_is_not_profit"]], ["missing_price_is_zero", "services/pnl.py", "(_multiply(balance, price))", "(_multiply(balance, price) or ZERO)", ["tests/test_financial_reference.py::test_missing_price_is_not_total_loss"]], ["buy_base_fee_ignored", "services/pnl.py", "amount = quantity - fee_in_base if side == \\"BUY\\" else quantity + fee_in_base", "amount = quantity if side == \\"BUY\\" else quantity + fee_in_base", ["tests/test_financial_reference.py::test_independent_fifo_references"]], ["quote_fee_ignored", "services/pnl.py", "value += converted if side == \\"BUY\\" else -converted", "value += ZERO", ["tests/test_financial_reference.py::test_independent_fifo_references"]], ["third_fee_missing_is_zero", "services/pnl.py", "missing.add(\\"MISSING_COMMISSION_CONVERSION\\")\\n                    value = None", "missing.add(\\"MISSING_COMMISSION_CONVERSION\\")\\n                    value = value", ["tests/test_financial_reference.py::test_unknown_commission_conversion_is_not_zero"]], ["global_trade_identity_lookup", "services/binance_trade_history.py", "SELECT * FROM binance_trades WHERE exchange=? AND symbol=? AND trade_id=?", "SELECT * FROM binance_trades WHERE exchange=? AND ? IS NOT NULL AND trade_id=?", ["tests/test_financial_reference.py::test_market_identity_collisions_and_replay"]], ["cursor_restarts_from_zero", "services/binance_trade_history.py", "cursor = state[\\"next_id\\"] if state else 0", "cursor = 0", ["tests/test_financial_reference.py::test_interruption_resume_and_process_restart"]], ["pagination_progress_check_removed", "services/binance_trade_history.py", "if next_cursor <= cursor or next_cursor > 9223372036854775807:", "if next_cursor > 9223372036854775807:", ["tests/test_financial_reference.py::test_pagination_repeating_page_fails_without_loop"]], ["invented_stablecoin_parity", "services/pnl.py", "if asset == target:", "if asset == target or asset == \'USDT\':", ["tests/test_financial_reference.py::test_stablecoins_are_not_assumed_at_parity"]], ["sell_base_fee_ignored", "services/pnl.py", "else quantity + fee_in_base", "else quantity", ["tests/test_financial_reference.py::test_independent_fifo_references"]], ["manual_position_overwritten", "services/binance_portfolio.py", "WHERE exchange=? AND synced_from_binance=1 AND symbol=? AND binance_asset IS NULL", "WHERE exchange=? AND symbol=? AND binance_asset IS NULL", ["tests/test_financial_sync.py::test_manual_position_same_market_is_preserved_byte_for_field"]], ["missing_price_closes_position", "services/binance_portfolio.py", "else \\"ACTIVE\\"\\n            else:", "else (\\"CLOSED\\" if value is None else \\"ACTIVE\\")\\n            else:", ["tests/test_financial_sync.py::test_price_failure_preserves_position_and_recovers"]], ["closed_realized_excluded", "services/binance_sync.py", "aggregate(\\"realized_pnl\\",rows)", "aggregate(\\"realized_pnl\\",active)", ["tests/test_financial_sync.py::test_sold_out_asset_is_discovered_without_current_holdings"]], ["missing_historical_fx_is_zero", "services/pnl.py", "if value is None:\\n        return None", "if value is None:\\n        return Decimal(0)", ["tests/test_financial_reference.py::test_unknown_commission_conversion_is_not_zero"]], ["funding_assumption_hidden", "services/pnl.py", "precisions.add(\\"FUNDING_HISTORY_NOT_DOCUMENTED\\")", "issues.add(\\"FUNDING_HISTORY_NOT_DOCUMENTED\\")", ["tests/test_financial_reference.py::test_spot_only_history_is_an_estimate_even_when_quantities_match"]], ["rate_limit_circuit_removed", "services/binance_portfolio.py", "if error.code in CIRCUIT_ERRORS:\\n                    raise", "if False:\\n                    raise", ["tests/test_financial_sync.py::test_global_outage_stops_market_loop_without_hammering_api"]], ["float_accounting_regression", "services/pnl.py", "result = Decimal(str(value))", "result = Decimal(float(value))", ["tests/test_financial_reference.py::test_independent_fifo_references"]], ["unknown_price_holding_hidden", "services/market_universe.py", "float(position.get(\\"quantity\\") or 0)>0 if value is None", "False if value is None", ["tests/test_financial_sync.py::test_price_outage_keeps_business_source_in_market_universe"]], ["manual_small_pnl_rounded", "services/pnl.py", "\\"gain_loss_amount\\":_number(gain),", "\\"gain_loss_amount\\":round(_number(gain),6) if gain is not None else None,", ["tests/test_financial_manual.py::test_manual_tiny_value_and_pnl_are_not_rounded_to_zero"]], ["manual_alpha_parity_invented", "services/personal_data.py", "if quote==\\"USDC\\":", "if quote in {\\"USDC\\",\\"USDT\\"}:", ["tests/test_financial_manual.py::test_manual_alpha_requires_real_quote_conversion"]], ["manual_closed_records_lost", "services/binance_sync.py", "\\"manual_realized_pnl_recorded\\":_number(manual_recorded) if manual_closed and not manual_unknown else None,", "\\"manual_realized_pnl_recorded\\":None,", ["tests/test_financial_manual.py::test_manual_closed_recorded_result_is_not_lost_or_called_verified"]]]')


def snapshot(files):
    result={}
    for name in files:
        if (ROOT/name).is_symlink():
            raise RuntimeError("Symlink sources are forbidden")
        result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def populate(directory,files):
    for name in files:
        source=ROOT/name
        if source.is_symlink():
            raise RuntimeError("Symlink sources are not allowed in mutation copies")
        target=directory/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)


def pytest_run(copy,selectors):
    run=subprocess.run([sys.executable,"-B",str(copy/"tools/verify_offline.py"),*selectors],
        cwd=copy,capture_output=True,text=True,timeout=45)
    audits=re.findall(r"ISOLATION_AUDIT=(\{[^\n]+\})",run.stdout)
    isolated=bool(audits) and not any(json.loads(audits[-1]).values())
    return run,isolated


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node",help="Optional Node executable for the financial display mutation")
    args=parser.parse_args()
    allowed_directories={"services","tests","static","templates","tools"}
    allowed_suffixes={".py",".js",".cjs",".css",".html",".sql"}
    allowed_root={"main.py","conftest.py","requirements.txt","README.md",".gitignore"}
    files=[name for name in sources() if name in allowed_root or
           (Path(name).parts[0] in allowed_directories and Path(name).suffix in allowed_suffixes)]
    before=snapshot(files)
    selectors=sorted({item for _,_,_,_,selection in MUTATIONS for item in selection})
    with tempfile.TemporaryDirectory(prefix="cryptoai-financial-baseline-") as temp:
        copy=Path(temp)
        populate(copy,files)
        baseline,isolated=pytest_run(copy,selectors)
        if baseline.returncode or not isolated:
            print(baseline.stdout[-6000:]+baseline.stderr[-1000:])
            print("FINANCIAL_MUTATIONS="+json.dumps({"baseline_passed":False,"isolation_ok":isolated}))
            return 1
    results=[]
    for name,filename,old,new,selection in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="cryptoai-financial-mutation-") as temp:
            copy=Path(temp)
            populate(copy,files)
            target=copy/filename
            original=target.read_text()
            if old not in original:
                raise RuntimeError("Mutation target missing: "+name)
            target.write_text(original.replace(old,new,1))
            run,isolated=pytest_run(copy,selection)
            detected=(run.returncode==1 and re.search(r"\b[1-9][0-9]* failed\b",run.stdout) is not None
                      and "ERROR collecting" not in run.stdout)
            result={"mutation":name,"detected":detected,"isolation_ok":isolated}
            results.append(result)
            print(json.dumps(result),flush=True)
            if not detected or not isolated:
                print(run.stdout[-6000:]+run.stderr[-1000:])
    if args.node:
        with tempfile.TemporaryDirectory(prefix="cryptoai-financial-js-mutation-") as temp:
            copy=Path(temp)
            for relative in ("static/app.js","tests/test_cockpit.js"):
                target=copy/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/relative,target)
            target=copy/"static/app.js"
            original=target.read_text()
            old="const totalPnl = pnl.total_pnl;"
            if old not in original:
                raise RuntimeError("Financial JS mutation target missing")
            target.write_text(original.replace(old,"const totalPnl = Number(pnl.total_pnl ?? 0);",1))
            script=str(copy/"tests/test_cockpit.js")
            if args.node.endswith(".exe") and os.name!="nt":
                script="\\\\"+"wsl.localhost\\Debian"+script.replace("/","\\")
            run=subprocess.run([args.node,script],capture_output=True,text=True,timeout=20)
            detected=run.returncode==1 and re.search(r"(?:fail|failing) [1-9]",run.stdout) is not None
            result={"mutation":"unknown_display_becomes_zero","detected":detected,
                    "isolation_ok":"NETWORK_ATTEMPTS=0" in run.stdout}
            results.append(result)
            print(json.dumps(result),flush=True)
            if not detected:
                print(run.stdout[-4000:]+run.stderr[-1000:])
    unchanged=snapshot(files)==before
    report={"baseline_passed":True,"detected":sum(row["detected"] for row in results),
            "total":len(results),"source_unchanged":unchanged,"results":results}
    print("FINANCIAL_MUTATIONS="+json.dumps(report),flush=True)
    return 0 if unchanged and all(row["detected"] and row["isolation_ok"] for row in results) else 1


if __name__=="__main__":
    raise SystemExit(main())
