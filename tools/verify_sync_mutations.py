"""Targeted synchronization mutations, restricted to disposable source copies."""
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
from tools.verify_financial_mutations import snapshot, populate, pytest_run

MUTATIONS = [['catalog_rescanned_incrementally', 'services/binance_sync_planner.py', 'candidates=(set(markets) if full_scan else explicit if mode=="bootstrap" and explicit else explicit|known|related)', 'candidates=set(markets)', ['tests/test_sync_planner.py::test_first_incremental_with_1666_markets_does_not_scan_them']], ['unexplored_history_hidden', 'services/binance_sync_planner.py', 'unknown=set(markets)-{s for s,row in states.items() if row["initial_complete"]}', 'unknown=set()-{s for s,row in states.items() if row["initial_complete"]}', ['tests/test_sync_planner.py::test_first_incremental_with_1666_markets_does_not_scan_them']], ['full_bootstrap_confirmation_removed', 'services/binance_sync_planner.py', 'if row["full_scan"] and not confirmed:', 'if False:', ['tests/test_sync_jobs.py::test_bootstrap_requires_preview_and_full_confirmation']], ['page_cancellation_ignored', 'services/binance_portfolio.py', 'if should_cancel and should_cancel():', 'if False:', ['tests/test_sync_jobs.py::test_cancellation_mid_page_writes_neither_page_nor_cursor']], ['resume_requeries_done_markets', 'services/binance_portfolio.py', 'if market["symbol"] in resume_completed:', 'if False:', ['tests/test_sync_jobs.py::test_interruption_between_markets_resumes_without_requerying_done_market']], ['headers_ignored', 'services/binance_api_budget.py', 'window["observed"] = max(window["observed"], count)', 'window["observed"] = max(window["observed"], 0)', ['tests/test_api_budget.py::test_published_limits_and_simultaneous_headers_never_refund']], ['weight_reservation_limit_removed', 'services/binance_api_budget.py', 'if self._usage(window) + amount > math.floor(limit["limit"] * self.safety):', 'if False:', ['tests/test_api_budget.py::test_local_fallback_budget_never_overbooks_concurrent_clients']], ['persistent_ban_ignored', 'services/binance_api_budget.py', 'if state["blocked_until"] > now:', 'if False:', ['tests/test_api_budget.py::test_bans_persist_across_client_restart_and_apply_to_public_calls']], ['429_not_suspended', 'services/binance_api_budget.py', 'if status in {429, 418}:', 'if status in {418}:', ['tests/test_api_budget.py::test_no_retry_after_429_and_no_request_during_suspension']], ['reset_purges_ban', 'services/history.py', '    "historical_fx_rates",', '    "historical_fx_rates",\n    "binance_api_state",', ['tests/test_api_budget.py::test_production_reset_preserves_persistent_ban']], ['reads_reacquire_write_lock', 'services/history.py', '    if _schema_ready(path):\n        return', '    if _schema_ready(path):\n        _initialize_db(path,allow_financial_migration=allow_financial_migration)\n        return', ['tests/test_sync_schema.py::test_read_path_does_not_begin_write_or_rebuild_schema']], ['job_result_owner_check_removed', 'services/binance_sync_jobs.py', 'WHERE id=? AND owner=? AND status IN (\'RUNNING\',\'CANCELLING\')""",\n                (status,self.clock()', 'WHERE id=? AND ? IS NOT NULL AND status IN (\'RUNNING\',\'CANCELLING\')""",\n                (status,self.clock()', ['tests/test_sync_jobs.py::test_stale_worker_cannot_replace_new_owner_result']], ['overlap_duplicates_not_counted', 'services/binance_sync_jobs.py', 'duplicates=duplicates+?,pages=pages+?', 'duplicates=duplicates+0*?,pages=pages+?', ['tests/test_sync_jobs.py::test_job_progress_counts_overlap_duplicates_without_reimport']]]

MUTATIONS.append(['stale_market_total_called_complete', 'services/binance_sync.py', ' and state["markets_unrefreshed"]==0', '', ['tests/test_sync_planner.py::test_fully_sold_new_market_between_incrementals_is_not_hidden_as_complete']])

MUTATIONS.extend([['public_catalog_metadata_ignored', 'services/binance_api_budget.py', 'if urlsplit(url).path == "/api/v3/exchangeInfo" and getattr(response, "status_code", 200) == 200:', 'if False:', ['tests/test_quota_regressions.py::test_first_public_catalog_without_keys_publishes_actual_limit']], ['spot_480_fallback_restored', 'services/binance_api_budget.py', '"SPOT": ("SPOT", 6000, 61000, 300)', '"SPOT": ("SPOT", 600, 61000, 300)', ['tests/test_quota_regressions.py::test_default_and_configured_fallbacks_are_network_specific']], ['cached_catalog_overwrites_fresh_limit', 'services/binance_api_budget.py', '(from_cache and state["published"])', 'False', ['tests/test_quota_regressions.py::test_cached_or_out_of_order_catalog_never_overwrites_newer_limits']], ['unknown_header_invents_600_ceiling', 'services/binance_api_budget.py', '# Unknown intervals are observable, not invented 600-unit limits.', 'if not any(x["metric"] == "REQUEST_WEIGHT" and x["seconds"] == seconds for x in state["limits"]):\n                        state["limits"].append({"metric": "REQUEST_WEIGHT", "seconds": seconds, "limit": 600, "source": "EXCHANGE_INFO"})', ['tests/test_quota_regressions.py::test_unknown_header_interval_observes_consumption_without_inventing_limit']], ['prior_window_header_poisoning', 'services/binance_api_budget.py', 'if math.floor(response_time / seconds) * seconds < window["start"]:', 'if False:', ['tests/test_quota_regressions.py::test_old_window_header_does_not_saturate_new_minute']], ['completed_reservations_never_confirmed', 'services/binance_api_budget.py', 'if record and record.get("charged", {}).get(metric_key) == window["start"]:\n                            window["unconfirmed"] = max(0, window["unconfirmed"] - record["weight"])', 'if record and record.get("charged", {}).get(metric_key) == window["start"]:\n                            window["unconfirmed"] = window["unconfirmed"]', ['tests/test_quota_regressions.py::test_completed_parallel_headers_retire_estimates_without_lowering_remote_observation']], ['orders_confused_with_ip_limit', 'services/binance_api_budget.py', 'metric not in {"REQUEST_WEIGHT", "RAW_REQUESTS"}', 'metric not in {"REQUEST_WEIGHT", "RAW_REQUESTS", "ORDERS"}', ['tests/test_quota_regressions.py::test_public_private_order_shares_limits_counts_and_no_extra_catalog']], ['redirects_issue_unreserved_requests', 'services/binance_api_budget.py', 'kwargs | {"allow_redirects": False}', 'kwargs | {"allow_redirects": True}', ['tests/test_quota_regressions.py::test_redirect_cannot_issue_unreserved_second_request']], ['wallet_shares_spot_limit', 'services/binance_api_budget.py', 'return network + "_SNAPSHOT"', 'return network', ['tests/test_quota_regressions.py::test_wallet_snapshot_has_independent_endpoint_budget_and_headers']], ['cold_catalog_single_fetch_removed', 'services/symbols.py', 'with _symbol_cache_lock:\n        now = time.time()', 'if True:\n        now = time.time()', ['tests/test_quota_regressions.py::test_parallel_cold_public_catalog_fetch_is_shared']], ['quota_btc_context_crashes_cockpit', 'main.py', 'if cause is None:\n                raise', 'if True:\n                raise', ['tests/test_quota_regressions.py::test_real_low_limit_refuses_before_transport_and_preserves_positions']], ['same_tick_response_relaxes_limit', 'services/binance_api_budget.py', 'matching["limit"] = min(matching["limit"], previous["limit"])', 'matching["limit"] = matching["limit"]', ['tests/test_quota_regressions.py::test_same_tick_conflicting_metadata_cannot_relax_official_policy']]])

MUTATIONS.append(['legacy_public_cockpit_480_reproduced', 'services/binance_api_budget.py', ['"SPOT": ("SPOT", 6000, 61000, 300)', 'if urlsplit(url).path == "/api/v3/exchangeInfo" and getattr(response, "status_code", 200) == 200:'], ['"SPOT": ("SPOT", 600, 61000, 300)', 'if False:'], ['tests/test_quota_regressions.py::test_full_dashboard_20_positions_6_watches_two_refreshes']])

MUTATIONS.append(['new_shorter_window_forgets_consumption', 'services/binance_api_budget.py', 'if not previously_enforced:', 'if False:', ['tests/test_quota_regressions.py::test_new_shorter_official_interval_preserves_known_consumption']])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node")
    args=parser.parse_args()
    directories={"services","tests","static","templates","tools"}
    suffixes={".py",".js",".cjs",".css",".html",".sql"}
    roots={"main.py","conftest.py","requirements.txt","README.md",".gitignore"}
    files=[name for name in sources() if name in roots or
           (Path(name).parts[0] in directories and Path(name).suffix in suffixes)]
    before=snapshot(files)
    selectors=sorted({s for *_,chosen in MUTATIONS for s in chosen})
    with tempfile.TemporaryDirectory(prefix="cryptoai-sync-baseline-") as directory:
        copy=Path(directory)
        populate(copy,files)
        baseline,isolated=pytest_run(copy,selectors)
        if baseline.returncode or not isolated:
            print(baseline.stdout[-7000:]+baseline.stderr[-1000:])
            print("SYNC_MUTATIONS="+json.dumps({"baseline_passed":False,"isolation_ok":isolated}))
            return 1
    results=[]
    for name,filename,old,new,chosen in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="cryptoai-sync-mutation-") as directory:
            copy=Path(directory)
            populate(copy,files)
            target=copy/filename
            original=target.read_text()
            edits=zip(old,new) if isinstance(old,list) else [(old,new)]
            for needle,replacement in edits:
                if needle not in original:
                    raise RuntimeError("Mutation target missing: "+name)
                original=original.replace(needle,replacement,1)
            target.write_text(original)
            run,isolated=pytest_run(copy,chosen)
            detected=run.returncode==1 and re.search(r"\b[1-9][0-9]* failed\b",run.stdout) is not None and "ERROR collecting" not in run.stdout
            result={"mutation":name,"detected":detected,"isolation_ok":isolated}
            results.append(result)
            print(json.dumps(result),flush=True)
            if not detected or not isolated:
                print(run.stdout[-5000:]+run.stderr[-1000:])
    if args.node:
        with tempfile.TemporaryDirectory(prefix="cryptoai-sync-js-mutation-") as directory:
            copy=Path(directory)
            for relative in ("static/app.js","tests/test_cockpit.js"):
                target=copy/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/relative,target)
            target=copy/"static/app.js"
            original=target.read_text()
            old='PARTIAL: "Historique ou données incomplets"'
            if old not in original:
                raise RuntimeError("Progress mutation target missing")
            target.write_text(original.replace(old,'PARTIAL: "Synchronisation terminée"',1))
            script=str(copy/"tests/test_cockpit.js")
            if args.node.endswith(".exe") and os.name!="nt":
                script="\\\\"+"wsl.localhost\\Debian"+script.replace("/","\\")
            run=subprocess.run([args.node,script],capture_output=True,text=True,timeout=20)
            detected=run.returncode==1 and re.search(r"(?:fail|failing) [1-9]",run.stdout) is not None
            results.append({"mutation":"incomplete_history_displayed_complete","detected":detected,
                            "isolation_ok":"NETWORK_ATTEMPTS=0" in run.stdout})
    unchanged=snapshot(files)==before
    report={"baseline_passed":True,"detected":sum(r["detected"] for r in results),
            "total":len(results),"source_unchanged":unchanged,"results":results}
    print("SYNC_MUTATIONS="+json.dumps(report),flush=True)
    return 0 if unchanged and all(r["detected"] and r["isolation_ok"] for r in results) else 1


if __name__=="__main__":
    raise SystemExit(main())
