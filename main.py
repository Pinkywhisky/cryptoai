import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import services.scanner as scanner
from services.backtesting import list_backtest_results
from services.alerts_engine import generate_ai_alerts
from services.binance_sync import (
    get_balances,
    get_account_summary,
    get_active_positions,
    get_binance_positions,
    get_binance_status,
    get_closed_trade_symbols,
    get_dust_balances,
    get_inactive_positions,
    get_pnl_summary,
    get_stored_trades,
    sync_binance_account,
)
from services.btc_correlation import calculate_relative_strength
from services.decision_engine import build_decision, build_decision_v5, build_decisions
from services.history import get_symbol_history, init_db, reset_database
from services.journal_analytics import compute_journal_analytics, compute_winrate
from services.market_universe import get_market_universe
from services.market_cache import get_market_payload_cache, save_market_payload
from services.opportunity_scanner import (
    get_market_candidates,
    is_constructive_opportunity,
)
from services.personal_data import (
    create_journal_entry,
    create_position,
    create_watch_candidate,
    cleanup_invalid_symbols,
    delete_journal_entry,
    delete_position,
    delete_watch_candidate,
    generate_all_alerts,
    list_journal,
    list_positions,
    list_watch_candidates,
    repair_market_sources,
    update_position,
    update_journal_entry,
    update_watch_candidate,
)
from services.score_history import get_score_evolution, save_score_snapshot

app = FastAPI(title="Crypto AI Assistant V5")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
init_db()
sync_in_progress = False
logger = logging.getLogger("crypto_ai.watchlist")


def api_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": message},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return api_error("Invalid request payload", status_code=422)


@app.exception_handler(Exception)
async def api_exception_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        logger.exception("Unhandled API error on %s", request.url.path)
        return api_error("Internal server error", status_code=500)
    raise exc


def _list_positions_for_decisions(enrich: bool = True):
    try:
        return list_positions(enrich=enrich)
    except TypeError:
        return list_positions()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"title": "Crypto AI Assistant V5"},
    )


@app.get("/api/watchlist")
def get_watchlist():
    return {"watchlist": scanner.WATCHLIST}


@app.get("/api/analyze/{symbol}")
def api_analyze(symbol: str, interval: str = "1h"):
    try:
        return scanner.analyze_symbol(symbol=symbol, interval=interval, limit=120)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/analyze-multi/{symbol}")
def api_analyze_multi(symbol: str):
    try:
        analysis = scanner.analyze_symbol_multi(symbol=symbol, limit=120)
        analysis["decision_engine"] = build_decision(
            symbol=symbol,
            analysis=analysis,
            scan_results=scanner.last_scan_results or [analysis],
            positions=_list_positions_for_decisions(),
        )
        return analysis
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/scan")
def api_scan(force: bool = False):
    payload = scanner.get_cached_scan(
        watchlist=scanner.WATCHLIST,
        limit=120,
        force=force,
    )
    raw_results = scanner.last_scan_results or payload.get("results", [])
    payload["results"] = scanner.enrich_scan_with_decisions(
        raw_results,
        positions=_list_positions_for_decisions(enrich=False),
    )
    for item in payload["results"]:
        decision = item.get("decision_engine")
        if decision and "error" not in item:
            save_score_snapshot(
                item["symbol"],
                decision,
                global_score=item.get("global_score"),
            )
    return payload


def _market_priority(item: dict) -> tuple[int, int, str]:
    decision = item.get("decision_engine", {}).get("decision")
    watch_priority = (item.get("watch_info") or {}).get("priority")
    has_position = "POSITION" in item.get("sources", [])
    decision_rank = {
        "CUT_LOSS": 0,
        "TAKE_PROFIT": 0,
        "BUY_READY": 1,
        "SELL_WATCH": 1,
        "BUY_WATCH": 2,
        "AVOID": 3,
        "WAIT": 4,
    }.get(decision, 5)
    if has_position and decision in {"CUT_LOSS", "TAKE_PROFIT", "SELL_WATCH", "BUY_READY", "BUY_WATCH"}:
        source_rank = 0
    elif has_position:
        source_rank = 1
    elif decision in {"BUY_READY", "TAKE_PROFIT", "CUT_LOSS"}:
        source_rank = 2
    elif watch_priority == "HIGH":
        source_rank = 3
    else:
        source_rank = int(item.get("priority", 99))
    return (source_rank, decision_rank, item.get("symbol", ""))


def _should_skip_market_analysis(entry: dict) -> bool:
    position_info = entry.get("position_info") or {}
    watch_info = entry.get("watch_info") or {}
    market_source = position_info.get("market_source") or watch_info.get("market_source")
    return bool(
        market_source in {"BINANCE_ALPHA", "MANUAL", "UNKNOWN"}
        or (
            "POSITION" in entry.get("sources", [])
            and position_info.get("analysis_available") is False
            and position_info.get("is_valid_symbol") is False
        )
    )


def _confidence_from_score(score: int | float | None, *, default: int = 50) -> int:
    if score is None:
        return default
    return max(30, min(75, int(score)))


def build_alpha_market_row(entry: dict) -> dict:
    position_info = entry.get("position_info") or {}
    watch_info = entry.get("watch_info") or {}
    source_info = position_info if position_info.get("market_source") else watch_info
    current_price = position_info.get("current_price") or watch_info.get("current_price")
    score = source_info.get("global_score")
    trend = source_info.get("global_trend") or "NEUTRAL"
    message = source_info.get("market_error") or "Analyse Alpha partielle"
    confidence = _confidence_from_score(score)
    logger.info(
        "[MARKET] Alpha row %s price=%s score=%s trend=%s",
        entry.get("symbol"),
        current_price,
        score,
        trend,
    )
    return {
        **entry,
        "symbol": entry["symbol"],
        "analysis_available": current_price is not None,
        "limited_analysis": True,
        "is_valid_symbol": True,
        "current_price": current_price,
        "global_score": score,
        "global_signal": "SURVEILLANCE",
        "global_trend": trend,
        "trend": trend,
        "setup_quality": source_info.get("setup_quality") or "LIMITED",
        "price_change_pct": source_info.get("price_change_pct"),
        "quote_volume": source_info.get("quote_volume"),
        "market_cap": source_info.get("market_cap"),
        "alpha_ma7": source_info.get("alpha_ma7"),
        "alpha_ma25": source_info.get("alpha_ma25"),
        "alpha_ma99": source_info.get("alpha_ma99"),
        "alpha_momentum_pct": source_info.get("alpha_momentum_pct"),
        "candles_available": source_info.get("candles_available", False),
        "decision_engine": {
            "decision": "WAIT",
            "decision_label": "Surveillance Alpha",
            "confidence": confidence,
            "trigger_label": "Alpha",
            "blocking_factors": [message],
            "positive_factors": ["Prix Alpha disponible"] if current_price is not None else [],
            "reason_summary": message,
        },
        "decision": "WAIT",
        "confidence": confidence,
        "trigger": "Alpha",
        "blocking_factor": message,
    }


def build_spot_market_row(entry: dict, analysis: dict, decision: dict) -> dict:
    logger.debug(
        "[MARKET] Spot row %s price=%s score=%s decision=%s",
        entry.get("symbol"),
        analysis.get("current_price"),
        analysis.get("global_score"),
        decision.get("decision"),
    )
    return {
        **analysis,
        **entry,
        "decision_engine": decision,
        "current_price": analysis.get("current_price"),
        "decision": decision.get("decision"),
        "confidence": decision.get("confidence"),
        "trend": analysis.get("global_trend"),
        "trigger": decision.get("trigger_label"),
        "blocking_factor": (decision.get("blocking_factors") or ["-"])[0],
    }


def _analysis_unavailable_row(entry: dict) -> dict:
    position_info = entry.get("position_info") or {}
    watch_info = entry.get("watch_info") or {}
    market_source = position_info.get("market_source") or watch_info.get("market_source")
    source_info = position_info if position_info.get("market_source") else watch_info
    current_price = position_info.get("current_price") or watch_info.get("current_price")
    if market_source == "BINANCE_ALPHA":
        return build_alpha_market_row(entry)

    message = source_info.get("market_error") or "Analyse indisponible"
    if position_info.get("is_valid_symbol") is False:
        message = "Paire Binance introuvable"
    elif watch_info.get("market_error"):
        message = watch_info["market_error"]
    return {
        **entry,
        "symbol": entry["symbol"],
        "error": message,
        "analysis_available": False,
        "is_valid_symbol": position_info.get("is_valid_symbol", market_source == "BINANCE_ALPHA"),
        "current_price": current_price,
        "decision": None,
        "confidence": None,
        "trend": "UNKNOWN",
        "trigger": "-",
        "blocking_factor": message,
    }


def _strip_unqualified_opportunity(row: dict, constructive_symbols: set[str]) -> dict | None:
    if "OPPORTUNITY_SCANNER" not in row.get("sources", []):
        return row
    if row.get("symbol") in constructive_symbols:
        return row

    sources = [source for source in row.get("sources", []) if source != "OPPORTUNITY_SCANNER"]
    badges = [badge for badge in row.get("badges", []) if badge != "Opportunité"]
    semantic_sources = {"POSITION", "WATCH_CANDIDATE", "WATCHLIST"}
    if not any(source in semantic_sources for source in sources):
        return None
    return {
        **row,
        "sources": sources,
        "badges": badges,
        "opportunity_info": None,
        "priority": 5 if sources == ["WATCHLIST"] else row.get("priority", 99),
    }


def _build_market_payload() -> dict:
    positions = list_positions()
    watch_candidates = list_watch_candidates()
    try:
        opportunities = get_market_candidates(top_n=30)
    except Exception:
        opportunities = [
            {
                "symbol": item.get("symbol"),
                "scan_score": item.get("global_score", 0),
                "reason": (item.get("summary_reasons") or ["Cache scan existant"])[0],
            }
            for item in (scanner.last_scan_results or [])
            if "error" not in item
        ][:10]

    universe = get_market_universe(
        positions=positions,
        watch_candidates=watch_candidates,
        watchlist=scanner.WATCHLIST,
        opportunities=opportunities,
    )
    symbols = [item["symbol"] for item in universe if not _should_skip_market_analysis(item)][:30]
    raw_results = scanner.scan_watchlist_multi(
        watchlist=symbols,
        limit=120,
        save_history=True,
    )
    raw_by_symbol = {item.get("symbol"): item for item in raw_results}
    decisions_by_symbol: dict[str, dict] = {}
    rows: list[dict] = []
    for entry in universe:
        symbol = entry["symbol"]
        if _should_skip_market_analysis(entry):
            rows.append(_analysis_unavailable_row(entry))
            continue
        analysis = raw_by_symbol.get(symbol, {"symbol": symbol, "error": "Analyse indisponible"})
        if "error" in analysis:
            rows.append({**entry, **analysis, "blocking_factor": analysis.get("error", "Analyse indisponible")})
            continue
        decision = build_decision(
            symbol=symbol,
            analysis=analysis,
            scan_results=raw_results,
            positions=positions,
        )
        decisions_by_symbol[symbol] = decision
        save_score_snapshot(symbol, decision, global_score=analysis.get("global_score"))
        rows.append(build_spot_market_row(entry, analysis, decision))

    constructive_symbols = {
        candidate["symbol"]
        for candidate in opportunities
        if is_constructive_opportunity(
            candidate,
            analysis=raw_by_symbol.get(candidate["symbol"]),
            decision=decisions_by_symbol.get(candidate["symbol"]),
        )
    }
    rows = [
        filtered
        for row in rows
        if (filtered := _strip_unqualified_opportunity(row, constructive_symbols)) is not None
    ]
    rows.sort(key=_market_priority)
    filtered_universe = [
        {key: row.get(key) for key in ["symbol", "sources", "badges", "priority", "position_info", "watch_info", "opportunity_info"]}
        for row in rows
    ]
    return {
        "results": rows,
        "universe": filtered_universe,
        "counts": {
            "positions": sum(1 for item in filtered_universe if "POSITION" in item["sources"]),
            "watch_candidates": sum(1 for item in filtered_universe if "WATCH_CANDIDATE" in item["sources"]),
            "opportunities": sum(1 for item in filtered_universe if "OPPORTUNITY_SCANNER" in item["sources"]),
            "total": len(filtered_universe),
        },
    }


@app.get("/api/market")
def api_market(force: bool = False):
    if not force:
        cached = get_market_payload_cache()
        if cached and cached.get("results"):
            return cached

    payload = _build_market_payload()
    if payload.get("results"):
        return save_market_payload(payload)
    return {**payload, "updated_at": None, "source": "fresh", "is_stale": False}


@app.get("/api/ai-alerts")
def api_ai_alerts(force: bool = False):
    market_payload = api_market(force=force)
    return {
        "alerts": generate_ai_alerts(market_payload.get("results", [])),
        "updated_at": market_payload.get("updated_at"),
        "source": market_payload.get("source"),
    }


@app.get("/api/history/{symbol}")
def api_history(symbol: str, limit: int = 50):
    return get_symbol_history(symbol=symbol, limit=limit)


@app.get("/api/top-momentum")
def api_top_momentum():
    return scanner.get_top_momentum(watchlist=scanner.WATCHLIST, limit=120, count=5)


@app.get("/api/top-setups")
def api_top_setups():
    return scanner.get_top_setups(watchlist=scanner.WATCHLIST, limit=120, count=5)


@app.get("/api/decision/{symbol}")
def api_decision(symbol: str):
    try:
        analysis = scanner.analyze_symbol_multi(symbol=symbol, limit=120)
        positions = _list_positions_for_decisions()
        scan_results = scanner.last_scan_results or [analysis]
        return build_decision(
            symbol=symbol,
            analysis=analysis,
            scan_results=scan_results,
            positions=positions,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/decision-v5/{symbol}")
def api_decision_v5(symbol: str):
    try:
        analysis = scanner.analyze_symbol_multi(symbol=symbol, limit=120)
        positions = _list_positions_for_decisions()
        scan_results = scanner.last_scan_results or [analysis]
        btc_analysis = next(
            (item for item in scan_results if item.get("symbol") == "BTCUSDC"),
            None,
        )
        return build_decision_v5(
            symbol=symbol,
            analysis=analysis,
            scan_results=scan_results,
            positions=positions,
            btc_analysis=btc_analysis,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/decisions")
def api_decisions(force: bool = False):
    scan_payload = scanner.get_cached_scan(
        watchlist=scanner.WATCHLIST,
        limit=120,
        force=force,
    )
    positions = _list_positions_for_decisions()
    raw_results = scanner.last_scan_results or scan_payload.get("results", [])
    return build_decisions(raw_results, positions=positions)


@app.get("/api/score-history/{symbol}")
def api_score_history(symbol: str, limit: int = 50):
    return get_score_evolution(symbol, limit=limit)


@app.get("/api/backtesting/results")
def api_backtesting_results(limit: int = 100):
    return list_backtest_results(limit=limit)


@app.get("/api/relative-strength/{symbol}")
def api_relative_strength(symbol: str):
    results = scanner.last_scan_results or scanner.get_cached_scan(
        watchlist=scanner.WATCHLIST,
        limit=120,
        force=False,
    ).get("results", [])
    symbol_item = next((item for item in results if item.get("symbol") == symbol.upper()), None)
    btc_item = next((item for item in results if item.get("symbol") == "BTCUSDC"), None)
    if not symbol_item or not btc_item:
        raise HTTPException(status_code=404, detail="Symbol or BTC context not found in current scan")
    return calculate_relative_strength(symbol_item, btc_item)


@app.get("/api/positions")
def api_positions():
    return list_positions()


@app.get("/api/positions/active")
def api_active_positions():
    return get_active_positions()


@app.get("/api/positions/inactive")
def api_inactive_positions():
    return get_inactive_positions()


@app.post("/api/positions")
def api_create_position(payload: dict):
    try:
        return create_position(payload)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except Exception:
        logger.exception("Position creation failed")
        return api_error("Unable to add position", status_code=500)


@app.put("/api/positions/{position_id}")
def api_update_position(position_id: int, payload: dict):
    try:
        position = update_position(position_id, payload)
        if not position:
            return api_error("Position not found", status_code=404)
        return position
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except Exception:
        logger.exception("Position update failed")
        return api_error("Unable to update position", status_code=500)


@app.delete("/api/positions/{position_id}")
def api_delete_position(position_id: int):
    if not delete_position(position_id):
        return api_error("Position not found", status_code=404)
    return {"deleted": True}


@app.get("/api/watch-candidates")
def api_watch_candidates():
    return list_watch_candidates()


@app.post("/api/watch-candidates")
def api_create_watch_candidate(payload: dict):
    try:
        candidate = create_watch_candidate(payload)
        logger.info("Watch candidate added: %s", candidate.get("symbol"))
        return candidate
    except ValueError as exc:
        logger.info("Watch candidate rejected: %s", exc)
        return api_error(str(exc), status_code=400)
    except Exception as exc:
        logger.exception("Watch candidate creation failed")
        return api_error("Unable to add watch candidate", status_code=500)


@app.put("/api/watch-candidates/{candidate_id}")
def api_update_watch_candidate(candidate_id: int, payload: dict):
    try:
        candidate = update_watch_candidate(candidate_id, payload)
        if not candidate:
            return api_error("Watch candidate not found", status_code=404)
        logger.info("Watch candidate updated: %s", candidate.get("symbol"))
        return candidate
    except ValueError as exc:
        logger.info("Watch candidate update rejected: %s", exc)
        return api_error(str(exc), status_code=400)
    except Exception:
        logger.exception("Watch candidate update failed")
        return api_error("Unable to update watch candidate", status_code=500)


@app.delete("/api/watch-candidates/{candidate_id}")
def api_delete_watch_candidate(candidate_id: int):
    if not delete_watch_candidate(candidate_id):
        logger.info("Watch candidate delete not found: %s", candidate_id)
        return api_error("Watch candidate not found", status_code=404)
    logger.info("Watch candidate deleted: %s", candidate_id)
    return {"deleted": True}


@app.get("/api/journal")
def api_journal():
    return list_journal()


@app.post("/api/journal")
def api_create_journal_entry(payload: dict):
    try:
        return create_journal_entry(payload)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)


@app.put("/api/journal/{entry_id}")
def api_update_journal_entry(entry_id: int, payload: dict):
    try:
        entry = update_journal_entry(entry_id, payload)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    if not entry:
        return api_error("Journal entry not found", status_code=404)
    return entry


@app.delete("/api/journal/{entry_id}")
def api_delete_journal_entry(entry_id: int):
    if not delete_journal_entry(entry_id):
        return api_error("Journal entry not found", status_code=404)
    return {"deleted": True}


@app.get("/api/journal/stats")
def api_journal_stats():
    entries = list_journal()
    return compute_winrate(entries)


@app.get("/api/journal/analytics")
def api_journal_analytics():
    entries = list_journal()
    return compute_journal_analytics(entries)


@app.get("/api/alerts")
def api_alerts():
    positions = list_positions()
    candidates = list_watch_candidates()
    return generate_all_alerts(positions, candidates)


@app.get("/api/binance/status")
def api_binance_status():
    return get_binance_status()


@app.post("/api/binance/sync")
def api_binance_sync():
    global sync_in_progress
    if sync_in_progress:
        return {
            "configured": True,
            "read_only_mode": True,
            "synced": False,
            "message": "Synchronisation Binance déjà en cours",
            "last_sync": get_binance_status().get("last_sync"),
        }
    sync_in_progress = True
    try:
        return sync_binance_account()
    except Exception as exc:
        return {
            "configured": True,
            "read_only_mode": True,
            "synced": False,
            "message": str(exc),
            "last_sync": get_binance_status().get("last_sync"),
        }
    finally:
        sync_in_progress = False


@app.get("/api/binance/balances")
def api_binance_balances():
    return get_balances()


@app.get("/api/binance/trades/{symbol}")
def api_binance_trades(symbol: str):
    return get_stored_trades(symbol)


@app.get("/api/binance/positions")
def api_binance_positions():
    return get_binance_positions()


@app.get("/api/binance/pnl")
def api_binance_pnl():
    return get_pnl_summary()


@app.get("/api/binance/account-summary")
def api_binance_account_summary():
    return get_account_summary()


@app.get("/api/binance/closed-trades")
def api_binance_closed_trades():
    return get_closed_trade_symbols()


@app.get("/api/binance/dust")
def api_binance_dust():
    return get_dust_balances()


@app.post("/api/admin/reset")
def api_admin_reset(reset_watchlist: bool = False):
    global sync_in_progress
    table_summary = reset_database()
    cache_summary = scanner.reset_scan_cache(reset_watchlist=reset_watchlist)
    sync_in_progress = False

    return {
        "reset": True,
        "tables": table_summary,
        "cache": {
            **cache_summary,
            "sync_in_progress": sync_in_progress,
        },
        "message": (
            "Données locales supprimées. Le marché peut réapparaître via la watchlist "
            "et les données live Binance."
        ),
    }


@app.post("/api/admin/cleanup-invalid-symbols")
def api_cleanup_invalid_symbols():
    return cleanup_invalid_symbols()


@app.post("/api/admin/repair-market-sources")
def api_repair_market_sources():
    return repair_market_sources()


@app.get("/analyze/{symbol}")
def analyze(symbol: str, interval: str = "1h"):
    return api_analyze(symbol=symbol, interval=interval)


@app.get("/scan")
def scan_market(interval: str = "1h"):
    return scanner.scan_watchlist(
        interval=interval,
        watchlist=scanner.WATCHLIST,
        limit=120,
    )
