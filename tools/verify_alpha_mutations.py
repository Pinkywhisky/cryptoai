"""Alpha regression mutations in disposable copies; production sources stay intact."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_security_mutations import sources
from tools.verify_financial_mutations import snapshot, populate, pytest_run

TEST = "tests/test_alpha_hardening.py::"
MUTATIONS = [
    ("invalid_pair_suspends_every_token", "services/binance_alpha.py",
     'if error.code != "INVALID_PAIR" and url != ALPHA_EXCHANGE_INFO_URL:', 'if True:',
     ["tests/test_alpha_real_regressions.py::test_invalid_then_valid_never_suspends"]),
    ("artificial_six_call_ceiling", "services/binance_api_budget.py",
     '_configured_limit("ALPHA_RAW_REQUESTS", 60)', '_configured_limit("ALPHA_RAW_REQUESTS", 6)',
     [TEST+"test_followed_tokens_cold_warm_and_three_refresh_cycles"]),
    ("old_eighty_unit_cost", "services/binance_api_budget.py",
     'weight = 1 if network == "ALPHA" else endpoint_weight(path, params)',
     'weight = 80 if network == "ALPHA" else endpoint_weight(path, params)',
     [TEST+"test_endpoint_accounting_unit_is_local_requests_not_fictional_weight"]),
    ("transient_classified_invalid", "services/symbols.py",
     'return {"valid": None, "symbol": raw, "market_source": MARKET_SOURCE_UNKNOWN,',
     'return {"valid": False, "symbol": raw, "market_source": MARKET_SOURCE_UNKNOWN,',
     [TEST+"test_catalog_outages_are_never_invalid_symbols"]),
    ("identification_downloads_analysis", "services/symbols.py",
     '    fetch_alpha_symbol,', '    get_alpha_market_data as fetch_alpha_symbol,',
     [TEST+"test_resolution_fetches_metadata_only_and_shares_catalog"]),
    ("expired_price_financial_leak", "services/binance_alpha.py",
     'return {**_unavailable(symbol, error, resolved), "market_data": state}',
     'return {**resolved, **data, "market_data": state, "price_available": True}',
     [TEST+"test_expired_price_is_only_labelled_metadata_and_not_financial_input"]),
    ("timestamp_refreshed_on_failure", "services/alpha_cache.py",
     'entry.retry_at = now+delay', 'entry.fetched_at = now\n                entry.retry_at = now+delay',
     [TEST+"test_cache_copy_isolation_lru_and_failure_timestamp"]),
    ("ten_minute_expiry_ignored", "services/alpha_cache.py",
     'ttl=600, stale_seconds=1800', 'ttl=6000, stale_seconds=1800',
     [TEST+"test_cache_expires_at_ten_minutes_and_discard_old_stale_values"]),
    ("undated_token_price_fallback", "services/binance_alpha.py",
     'price = _safe_float(data.get("lastPrice", data.get("price")))',
     'price = _safe_float(data.get("lastPrice", data.get("price")) or resolved["token"].get("price"))',
     [TEST+"test_bad_ticker_never_falls_back_to_undated_catalog_price"]),
    ("http_bans_ignored", "services/binance_api_budget.py",
     'if status in {429, 418}:', 'if False:',
     [TEST+"test_retry_after_is_durable_and_not_retried"]),
    ("spot_headers_poison_alpha", "services/binance_api_budget.py",
     'match = re.fullmatch(pattern, key) if network != "ALPHA" else None',
     'match = re.fullmatch(pattern, key)',
     [TEST+"test_optional_exhausted_headers_never_import_spot_limits"]),
    ("malformed_catalog_confirms_absence", "services/binance_alpha.py",
     'if not isinstance(data, list) or not data:\n        raise MarketDataError("INVALID_RESPONSE")',
     'if not isinstance(data, list) or not data:\n        return {}',
     [TEST+"test_malformed_catalog_cannot_confirm_absence"]),
    ("cleanup_deletes_unconfirmed_symbol", "services/personal_data.py",
     'if resolutions[row["symbol"]].get("valid") is False:',
     'if not resolutions[row["symbol"]].get("valid"):',
     [TEST+"test_cleanup_and_repair_defer_outages_without_deleting_or_reclassifying"]),
    ("repair_reclassifies_on_outage", "services/personal_data.py",
     'if resolution.get("valid") is None:\n                continue',
     'if False:\n                continue',
     [TEST+"test_cleanup_and_repair_defer_outages_without_deleting_or_reclassifying"]),
    ("outer_cache_extends_quote_lifetime", "services/market_cache.py",
     'or time.time() >= deadline', 'or False',
     [TEST+"test_cockpit_propagates_current_stale_and_recovery_without_buy_signal"]),
    ("receipt_hides_old_exchange_time", "services/binance_alpha.py",
     'as_of, basis = stamp/1000, "EXCHANGE_TIME"',
     'as_of, basis = _clock(), "EXCHANGE_TIME"',
     [TEST+"test_server_timestamp_not_receipt_controls_quote_freshness"]),
    ("duplicate_symbol_last_wins", "services/binance_alpha.py",
     'if token.get("ambiguous"):', 'if False:',
     [TEST+"test_ambiguous_catalog_symbol_is_never_silently_last_wins"]),
    ("transport_concurrency_unbounded", "services/binance_alpha.py",
     'threading.BoundedSemaphore(2)', 'threading.BoundedSemaphore(100)',
     [TEST+"test_different_tokens_transport_concurrency_is_bounded"]),
    ("stale_analysis_is_current", "services/binance_alpha.py",
     'if read.value and read.value[-1]["close_time"]/1000 < _clock()-INTERVAL_SECONDS[interval]-CACHE_TTL_SECONDS:',
     'if False:',
     [TEST+"test_old_candles_cannot_create_current_technical_analysis"]),
]

MUTATIONS.extend([
    ("server_time_does_not_expire_cache", "services/binance_alpha.py",
     'valid_until=lambda value: value["as_of"]+CACHE_TTL_SECONDS', 'valid_until=None',
     [TEST+"test_server_stale_quote_retries_after_cooldown_instead_of_caching_for_ten_minutes",
      TEST+"test_recent_response_with_old_quote_is_only_dated_metadata"]),
    ("alpha_retry_rescans_spot", "main.py", "refreshed = _refresh_cached_alpha(cached)",
     "refreshed = None", [TEST+"test_alpha_retry_refreshes_only_alpha_and_preserves_spot_cache_date"]),
    ("alpha_refresh_resurrects_invalidated_cache", "services/market_cache.py",
     "UPDATE market_cache SET payload_json=? WHERE id=? AND updated_at=?",
     "INSERT OR REPLACE INTO market_cache(payload_json,id,updated_at) VALUES(?,?,?)",
     [TEST+"test_alpha_refresh_cannot_resurrect_invalidated_or_replace_newer_cache"]),
])


REVIEW = "tests/test_alpha_final_review.py::"
MUTATIONS.extend([
    ("service_retry_after_ignored", "services/binance_api_budget.py",
     'if network == "ALPHA" and 500 <= status < 600:', 'if False:',
     [REVIEW+"test_server_retry_after_120_is_shared_and_not_retried"]),
    ("service_suspension_capped_at_sixty", "services/binance_api_budget.py",
     'seconds = max(1, seconds)', 'seconds = min(60, max(1, seconds))',
     [REVIEW+"test_suspend_alpha_respects_120_and_rejects_nonfinite"]),
    ("negative_state_rebuilt_every_five_seconds", "services/binance_alpha.py",
     'retry_after=min(remaining) if remaining else CACHE_TTL_SECONDS', 'retry_after=5',
     [REVIEW+"test_negative_deadline_matches_catalog_and_does_not_refresh_early"]),
    ("stable_contract_identity_ignored", "services/binance_alpha.py",
     'for key in ("alphaId", "chainId", "contractAddress"))]', 'for key in ("alphaId",))]',
     [REVIEW+"test_two_active_candidates_require_complete_identity"]),
    ("conflicting_status_used_as_fresh", "services/binance_alpha.py",
     'if resolved.get("status_error"):', 'if False:',
     [REVIEW+"test_offline_trading_conflict_indicative_only_and_recovery"]),
    ("catalog_change_does_not_invalidate_snapshot", "services/market_cache.py",
     'or newer_catalog):', '):',
     [REVIEW+"test_newer_catalog_invalidates_negative_outer_snapshot"]),
    ("unchanged_snapshot_written_again", "services/market_cache.py",
     'if normalize(previous) == normalize(stored):', 'if False:',
     [REVIEW+"test_unchanged_snapshot_causes_no_sqlite_update"]),
    ("bad_exchange_envelope_suspends_tickers", "services/binance_alpha.py",
     'and url != ALPHA_EXCHANGE_INFO_URL:', ':',
     [REVIEW+"test_malformed_exchange_envelope_degrades_without_global_suspend"]),
    ("offline_conflict_always_probes_ticker", "services/binance_alpha.py",
     'if verify_conflict:', 'if True:',
     [REVIEW+"test_offline_trading_conflict_indicative_only_and_recovery"]),
    ("preview_currency_not_cached", "services/binance_alpha.py",
     'last_known_price_quote_asset=check.value["quote_asset"]', 'last_known_price_quote_asset=resolved["quote_asset"]',
     [REVIEW+"test_conflict_fallback_keeps_validated_currency_on_repeated_preview"]),
])


def javascript_run(node, copy):
    script = str(copy/"tests/test_cockpit.js")
    if node.endswith(".exe") and os.name != "nt":
        script = subprocess.check_output(["wslpath", "-w", script], text=True).strip()
    # stdin require avoids the Windows Node --test glob bug on UNC paths.
    return subprocess.run([node, "-"], input="require("+json.dumps(script)+");",
                          capture_output=True, text=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dirs = {"services", "tests", "static", "templates", "tools"}
    suffixes = {".py", ".js", ".cjs", ".css", ".html", ".sql", ".json"}
    roots = {"main.py", "conftest.py", "requirements.txt", "README.md", ".gitignore"}
    files = [f for f in sources() if f in roots or
             Path(f).parts[0] in dirs and Path(f).suffix in suffixes]
    before = snapshot(files)
    results = []
    with tempfile.TemporaryDirectory(prefix="cryptoai-alpha-mutations-") as directory:
        copy = Path(directory)
        populate(copy, files)
        baseline, isolated = pytest_run(copy, ["tests/test_alpha_hardening.py", "tests/test_alpha_real_regressions.py", "tests/test_alpha_final_review.py"])
        if baseline.returncode or not isolated:
            raise RuntimeError("Mutation baseline failed: "+baseline.stdout[-5000:]+baseline.stderr[-1000:])
    for name, filename, old, new, selected in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="cryptoai-alpha-mutant-") as directory:
            copy = Path(directory)
            populate(copy, files)
            target = copy/filename
            original = target.read_text()
            if old not in original:
                raise RuntimeError("Missing mutation target: "+name)
            target.write_text(original.replace(old, new, 1))
            run, isolated = pytest_run(copy, selected)
            detected = (run.returncode == 1 and re.search(r"\b[1-9][0-9]* failed\b", run.stdout)
                        is not None and "ERROR collecting" not in run.stdout)
            result = {"mutation": name, "detected": detected, "isolation_ok": isolated}
            results.append(result)
            print(json.dumps(result), flush=True)
            if not detected or not isolated:
                print(run.stdout[-5000:]+run.stderr[-1000:])
    if args.node:
        for name, old, new in [
            ("browser_price_expiry_ignored", "else if (now - asOf >= 600) state = \"STALE\";",
             'else if (false) state = "STALE";'),
            ("browser_stale_price_leak", 'const available = !state || ["FRESH", "PARTIAL"].includes(state.state);',
             'const available = true;'),
        ]:
            with tempfile.TemporaryDirectory(prefix="cryptoai-alpha-js-mutant-") as directory:
                copy = Path(directory)
                populate(copy, ["static/app.js", "tests/test_cockpit.js"])
                target = copy/"static/app.js"
                original = target.read_text()
                if old not in original:
                    raise RuntimeError("Missing JavaScript mutation target")
                target.write_text(original.replace(old, new, 1))
                run = javascript_run(args.node, copy)
                results.append({"mutation": name, "detected": run.returncode == 1
                                and re.search(r"(?:fail|failing) [1-9]", run.stdout) is not None,
                                "isolation_ok": "NETWORK_ATTEMPTS=0" in run.stdout})
    report = {"baseline_passed": True, "total": len(results),
              "detected": sum(r["detected"] for r in results),
              "source_unchanged": before == snapshot(files), "results": results}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2)+"\n")
    print("ALPHA_MUTATIONS="+json.dumps(report))
    return 0 if report["source_unchanged"] and all(r["detected"] and r["isolation_ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
