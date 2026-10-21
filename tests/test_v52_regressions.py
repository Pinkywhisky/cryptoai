"""Regressions found during the independent V5.2 review (no external data)."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import main
import services.decision_engine as engine
import services.market_cache as market_cache
import services.personal_data as personal
import services.scanner as scanner
from services.history import DB_PATH
from services.opportunity_scanner import is_constructive_opportunity


def analysis(symbol="ETHUSDC", **overrides):
    result = {
        "symbol": symbol,
        "global_score": 70,
        "global_signal": "ACHAT POTENTIEL",
        "global_trend": "BULLISH",
        "current_price": 100,
        "support": 96,
        "distance_to_resistance_pct": 10,
        "context_score": 70,
        "setup_score": 70,
        "trigger_score": 70,
        "risk_score": 65,
        "momentum": {"medium": {"direction": "BULLISH", "change_pct": 7 if symbol != "BTCUSDC" else 1}},
        "patterns": {},
        "timeframes": {},
    }
    result.update(overrides)
    return result


def market_context(**overrides):
    return {"market_score": 60, "btc_trend": "NEUTRAL", **overrides}


@pytest.fixture
def local_market(monkeypatch):
    monkeypatch.setattr(scanner, "WATCHLIST", [])
    monkeypatch.setattr(scanner, "last_scan_results", None)
    monkeypatch.setattr(scanner, "last_scan_timestamp", None)
    monkeypatch.setattr(scanner, "last_scan_key", None)
    monkeypatch.setattr(scanner, "analyze_symbol_multi", lambda symbol, limit=120: analysis(symbol))
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [])
    monkeypatch.setattr(main, "list_positions", lambda enrich=True: personal.list_positions(enrich=False))
    monkeypatch.setattr(main, "list_watch_candidates", lambda enrich=True: personal.list_watch_candidates(enrich=False))
    monkeypatch.setattr(personal, "resolve_user_market_symbol", lambda payload: {
        "symbol": payload["symbol"].upper(),
        "resolved_symbol": payload["symbol"].upper(),
        "base_asset": payload["symbol"].upper().removesuffix("USDC"),
        "market_source": "BINANCE_SPOT",
    })


@pytest.mark.parametrize("code", ["BUY_READY", "BUY_WATCH"])
@pytest.mark.parametrize("rejection", [
    "low_scan_score", "very_bearish", "fake_breakout", "low_context",
    "low_setup", "low_market", "low_risk", "low_confidence",
])
def test_buy_decision_cannot_bypass_opportunity_qualification(monkeypatch, code, rejection):
    candidate = {"symbol": "ETHUSDC", "scan_score": 80}
    raw = analysis()
    decision = {
        "decision": code, "confidence": 70, "context_score": 70,
        "setup_score": 70, "market_score": 60, "risk_score": 65,
    }
    if rejection == "low_scan_score":
        candidate["scan_score"] = 24
    elif rejection == "very_bearish":
        raw["global_trend"] = "VERY_BEARISH"
    elif rejection == "fake_breakout":
        raw["advanced_triggers"] = {"signals": [{"label": "Fake breakout", "detected": True}]}
    else:
        key = {"low_context": "context_score", "low_setup": "setup_score",
               "low_market": "market_score", "low_risk": "risk_score", "low_confidence": "confidence"}[rejection]
        decision[key] = 19
    assert is_constructive_opportunity(candidate, analysis=raw, decision=decision) is False
    monkeypatch.setattr(main, "list_positions", lambda: [])
    monkeypatch.setattr(main, "list_watch_candidates", lambda: [])
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [candidate])
    monkeypatch.setattr(scanner, "scan_watchlist_multi", lambda **kwargs: [raw])
    monkeypatch.setattr(main, "build_decision", lambda **kwargs: decision)
    response = TestClient(main.app).get("/api/market?force=true")
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["universe"] == []
    assert response.json()["counts"]["opportunities"] == 0


@pytest.mark.parametrize("code", ["BUY_READY", "BUY_WATCH"])
def test_qualified_opportunity_still_visible_and_cockpit_uses_base_engine(monkeypatch, local_market, code):
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [{"symbol": "ETHUSDC", "scan_score": 80}])
    monkeypatch.setattr(scanner, "analyze_symbol_multi", lambda symbol, limit=120: analysis(
        symbol, trigger_score=70 if code == "BUY_READY" else 45,
    ))

    def unexpected_v5(**kwargs):
        pytest.fail("The cockpit must keep using the existing decision engine")
    monkeypatch.setattr(main, "build_decision_v5", unexpected_v5)
    payload = TestClient(main.app).get("/api/market?force=true").json()
    assert payload["counts"]["opportunities"] == 1
    assert payload["results"][0]["decision_engine"]["decision"] == code


@pytest.mark.parametrize("remaining", ["none", "qualified_opportunity", "rejected_opportunity", "position"])
def test_deleting_watch_rebuilds_business_sources_without_resurrection(monkeypatch, local_market, remaining):
    candidate = personal.create_watch_candidate({"symbol": "ETHUSDC"})
    if "opportunity" in remaining:
        monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [
            {"symbol": "ETHUSDC", "scan_score": 80 if remaining == "qualified_opportunity" else 24},
        ])
    if remaining == "position":
        monkeypatch.setattr(main, "list_positions", lambda enrich=True: [{
            "symbol": "ETHUSDC", "source": "MANUAL", "status": "MANUAL",
            "is_active": 1, "quantity": 1, "market_source": "BINANCE_SPOT",
        }])
    client = TestClient(main.app)
    before = client.get("/api/market?force=true").json()
    assert before["counts"]["watch_candidates"] == 1
    assert market_cache.get_market_payload_cache() is not None
    assert client.delete(f"/api/watch-candidates/{candidate['id']}").json() == {"deleted": True}
    assert market_cache.get_market_payload_cache() is None
    after = client.get("/api/market").json()
    assert after["counts"]["watch_candidates"] == 0
    assert personal.list_watch_candidates(enrich=False) == []
    if remaining in {"none", "rejected_opportunity"}:
        assert after["results"] == []
        assert client.get("/api/market").json()["results"] == []
    else:
        row = after["results"][0]
        assert row["watch_info"] is None
        assert "WATCH_CANDIDATE" not in row["sources"]
        assert ("POSITION" if remaining == "position" else "OPPORTUNITY_SCANNER") in row["sources"]
        assert after["counts"]["total"] == 1


def cached_payload():
    return {
        "results": [{"symbol": "ETHUSDC", "sources": ["WATCH_CANDIDATE", "BINANCE_SPOT"]}],
        "universe": [{"symbol": "ETHUSDC", "sources": ["WATCH_CANDIDATE", "BINANCE_SPOT"]}],
    }


@pytest.mark.parametrize("legacy", ["unversioned", "old_version", "watchlist_results", "watchlist_universe"])
def test_incompatible_market_cache_is_ignored_and_rebuilt(monkeypatch, local_market, legacy):
    payload = cached_payload()
    if legacy != "unversioned":
        payload["cache_version"] = market_cache.MARKET_CACHE_VERSION if legacy.startswith("watchlist") else 1
    if legacy.startswith("watchlist"):
        payload[legacy.removeprefix("watchlist_")][0]["sources"] = ["WATCHLIST", "BINANCE_SPOT"]
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("INSERT INTO market_cache (id, payload_json, updated_at) VALUES (1, ?, ?)",
                           (json.dumps(payload), datetime.now(UTC).isoformat()))
    assert market_cache.get_market_payload_cache() is None
    response = TestClient(main.app).get("/api/market")
    assert response.status_code == 200
    assert response.json()["results"] == []


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
@pytest.mark.parametrize("invalidation_fails", [False, True])
def test_watch_mutation_and_cache_invalidation_are_one_transaction(monkeypatch, local_market, operation, invalidation_fails):
    candidate = None if operation == "create" else personal.create_watch_candidate({"symbol": "ETHUSDC", "priority": "LOW"})
    before = personal.list_watch_candidates(enrich=False)
    market_cache.save_market_payload(cached_payload())
    if invalidation_fails:
        def fail_invalidation(connection):
            raise sqlite3.OperationalError("Simulated cache invalidation failure")
        monkeypatch.setattr(personal, "invalidate_market_cache", fail_invalidation)
    client = TestClient(main.app, raise_server_exceptions=False)
    if operation == "create":
        response = client.post("/api/watch-candidates", json={"symbol": "ETHUSDC"})
    elif operation == "update":
        response = client.put(f"/api/watch-candidates/{candidate['id']}", json={"priority": "HIGH"})
    else:
        response = client.delete(f"/api/watch-candidates/{candidate['id']}")
    after = personal.list_watch_candidates(enrich=False)
    if invalidation_fails:
        assert response.status_code == 500
        assert response.json()["success"] is False
        assert after == before
        assert market_cache.get_market_payload_cache()["results"] == cached_payload()["results"]
    else:
        assert response.status_code == 200
        assert market_cache.get_market_payload_cache() is None
        if operation == "delete":
            assert after == []
        elif operation == "update":
            assert after[0]["priority"] == "HIGH"
        else:
            assert after[0]["symbol"] == "ETHUSDC"


@pytest.mark.parametrize("route", ["/api/scan?force=true", "/api/decisions?force=true",
                                   "/api/top-momentum", "/api/top-setups", "/scan"])
def test_legacy_routes_scan_new_universe_when_watchlist_empty(monkeypatch, local_market, route):
    monkeypatch.setattr(main, "list_positions", lambda enrich=True: [
        {"symbol": "BTCUSDC", "source": "BINANCE", "is_active": 1, "status": "ACTIVE", "current_value": 100},
        {"symbol": "ADAUSDC", "source": "MANUAL", "is_active": 1, "status": "MANUAL", "market_source": "BINANCE_SPOT"},
        {"symbol": "DUSTUSDC", "source": "BINANCE", "is_active": 1, "status": "DUST", "current_value": 0.1},
        {"symbol": "CLOSEDUSDC", "source": "BINANCE", "is_active": 0, "status": "CLOSED", "current_value": 0},
    ])
    monkeypatch.setattr(main, "list_watch_candidates", lambda enrich=True: [
        {"symbol": "ETHUSDC", "market_source": "BINANCE_SPOT"},
        {"symbol": "CKP", "market_source": "BINANCE_ALPHA"},
    ])
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [{"symbol": "SUIUSDC", "scan_score": 80}])
    monkeypatch.setattr(scanner, "analyze_symbol", lambda symbol, interval, limit: {
        "symbol": symbol, "analysis": {"score": 70, "signal": "ATTENDRE", "price": 100, "reasons": ["Simulated"]},
    })
    response = TestClient(main.app).get(route)
    assert response.status_code == 200
    payload = response.json()
    rows = payload["results"] if isinstance(payload, dict) else payload
    assert {row["symbol"] for row in rows} == {"BTCUSDC", "ADAUSDC", "ETHUSDC", "SUIUSDC"}


def test_legacy_explicit_watchlist_still_takes_precedence(monkeypatch, local_market):
    monkeypatch.setattr(scanner, "WATCHLIST", ["ETHUSDC"])
    discoveries = []
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: discoveries.append(True) or [])
    response = TestClient(main.app).get("/api/scan?force=true")
    assert response.status_code == 200
    assert [row["symbol"] for row in response.json()["results"]] == ["ETHUSDC"]
    assert discoveries == []


def test_relative_strength_adds_btc_and_does_not_reuse_partial_scan_cache(monkeypatch, local_market):
    personal.create_watch_candidate({"symbol": "ETHUSDC"})
    client = TestClient(main.app)
    assert [row["symbol"] for row in client.get("/api/scan?force=true").json()["results"]] == ["ETHUSDC"]
    assert scanner.last_scan_key == (("ETHUSDC",), 120)
    response = client.get("/api/relative-strength/ethusdc")
    assert response.status_code == 200
    assert response.json()["spread_pct"] == 6
    assert {row["symbol"] for row in scanner.last_scan_results} == {"ETHUSDC", "BTCUSDC"}
    assert client.get("/api/relative-strength/UNKNOWNUSDC").status_code == 404


@pytest.mark.parametrize("function", [scanner.scan_watchlist, scanner.scan_watchlist_multi])
def test_explicit_empty_scan_does_not_fall_back_to_legacy_watchlist(monkeypatch, function):
    monkeypatch.setattr(scanner, "WATCHLIST", ["SHOULD_NOT_SCAN"])
    assert function(watchlist=[]) == []


def test_scan_cache_is_keyed_by_symbols_and_limit(monkeypatch, local_market):
    calls = []
    def fake_scan(watchlist, limit, save_history):
        calls.append((watchlist, limit))
        return [analysis(symbol) for symbol in watchlist]
    monkeypatch.setattr(scanner, "scan_watchlist_multi", fake_scan)
    scanner.get_cached_scan(["BTCUSDC"])
    scanner.get_cached_scan(["BTCUSDC"])
    scanner.get_cached_scan(["BTCUSDC", "ETHUSDC"])
    scanner.get_cached_scan(["BTCUSDC", "ETHUSDC"], limit=150)
    assert calls == [(["BTCUSDC"], 120), (["BTCUSDC", "ETHUSDC"], 120), (["BTCUSDC", "ETHUSDC"], 150)]


@pytest.mark.parametrize("overrides,context,positions,expected", [
    ({"risk_score": 25}, {}, [], "AVOID"),
    ({"global_trend": "VERY_BEARISH"}, {}, [], "AVOID"),
    ({}, {"btc_trend": "VERY_BEARISH"}, [], "WAIT"),
    ({}, {"market_score": 30}, [], "WAIT"),
    ({"risk_score": 45}, {}, [], "WAIT"),
    ({"global_trend": "BEARISH", "current_price": 90}, {}, [
        {"symbol": "ETHUSDC", "gain_loss_pct": -10, "max_loss_accepted_pct": 5},
    ], "CUT_LOSS"),
    ({"distance_to_resistance_pct": 1}, {}, [{"symbol": "ETHUSDC", "gain_loss_pct": 10}], "TAKE_PROFIT"),
    ({"global_trend": "BEARISH"}, {}, [{"symbol": "ETHUSDC"}], "SELL_WATCH"),
])
def test_technical_timing_never_overrides_risk_or_position_decision(overrides, context, positions, expected):
    result = engine.build_decision("ETHUSDC", analysis(**overrides), positions=positions,
                                   market_context=market_context(**context))
    assert result["decision"] == expected
    assert result["trigger_score"] == 70
    assert result["timing_status"] == "CONFIRMED"
    assert result["timing_is_technical_signal"] is True
    assert result["timing_is_actionable"] is False
    assert result["timing_action"] != "Surveiller une entree possible"


@pytest.mark.parametrize("acceleration,expected", [("NONE", "WAIT"), ("STRONG", "BUY_WATCH")])
def test_v5_trigger_bonus_is_technical_and_does_not_authorize_buy(acceleration, expected):
    raw = analysis(trigger_score=50, setup_score=52)
    result = engine.build_decision_v5("ETHUSDC", raw, scan_results=[raw, analysis("BTCUSDC")],
                                      score_acceleration={"acceleration": acceleration},
                                      advanced_triggers={"detected": True, "score": 100})
    assert result["decision"] == expected
    assert result["trigger_score"] == 68
    assert result["timing_is_technical_signal"] is True
    assert result["timing_is_actionable"] is False


def test_v5_timing_reflects_final_btc_veto(monkeypatch):
    monkeypatch.setattr(engine, "btc_altcoin_adjustment", lambda *args: {"adjustment": -18, "blocking": True})
    raw, btc = analysis(), analysis("BTCUSDC")
    result = engine.build_decision_v5("ETHUSDC", raw, scan_results=[raw, btc], btc_analysis=btc,
                                      score_acceleration={"acceleration": "NONE"})
    assert result["decision"] == "BUY_WATCH"
    assert result["timing_status"] == "CONFIRMED"
    assert result["timing_is_actionable"] is False
    assert result["timing_action"] != "Surveiller une entree possible"
