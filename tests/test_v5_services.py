import pandas as pd
from fastapi.testclient import TestClient

import main as app_module
from services.advanced_triggers import (
    detect_fake_breakout,
    detect_momentum_acceleration,
    detect_volatility_squeeze,
)
from services.backtesting import calculate_backtest_metrics, list_backtest_results, save_backtest_result
from services.btc_correlation import calculate_relative_strength
from services.market_heatmap import generate_market_heatmap
from services.market_regime import determine_market_regime
from services.opportunity_scanner import (
    filter_constructive_opportunities,
    get_market_candidates,
    is_constructive_opportunity,
    is_eligible_symbol,
)
from services.score_history import calculate_score_acceleration, get_score_evolution, save_score_snapshot


client = TestClient(app_module.app)


def test_opportunity_scanner_excludes_stables_and_low_volume():
    tickers = [
        {"symbol": "USDTUSDC", "quoteVolume": "99999999", "priceChangePercent": "1", "highPrice": "1", "lowPrice": "1", "lastPrice": "1", "volume": "1"},
        {"symbol": "ABCUPUSDC", "quoteVolume": "99999999", "priceChangePercent": "8", "highPrice": "2", "lowPrice": "1", "lastPrice": "2", "volume": "1000"},
        {"symbol": "LOWUSDC", "quoteVolume": "100", "priceChangePercent": "20", "highPrice": "2", "lowPrice": "1", "lastPrice": "2", "volume": "1000"},
        {"symbol": "SUIUSDC", "quoteVolume": "8000000", "priceChangePercent": "5", "highPrice": "1.02", "lowPrice": "0.90", "lastPrice": "1.015", "volume": "1000000", "weightedAvgPrice": "1"},
    ]

    candidates = get_market_candidates(tickers, min_quote_volume=1_000_000)

    assert [item["symbol"] for item in candidates] == ["SUIUSDC"]
    assert candidates[0]["scan_score"] > 40
    assert not is_eligible_symbol("USDTUSDC")


def test_opportunity_filter_rejects_avoid_and_destroyed_setups():
    candidate = {"symbol": "BADUSDC", "scan_score": 90}
    avoid_decision = {
        "decision": "AVOID",
        "context_score": 50,
        "setup_score": 50,
        "risk_score": 60,
        "market_score": 50,
        "confidence": 60,
    }
    weak_setup = {
        "decision": "WAIT",
        "context_score": 50,
        "setup_score": 25,
        "risk_score": 60,
        "market_score": 50,
        "confidence": 60,
    }

    assert not is_constructive_opportunity(candidate, analysis={"global_trend": "NEUTRAL"}, decision=avoid_decision)
    assert not is_constructive_opportunity(candidate, analysis={"global_trend": "NEUTRAL"}, decision=weak_setup)


def test_opportunity_filter_keeps_constructive_wait_and_prioritizes_buy_ready():
    candidates = [
        {"symbol": "WAITUSDC", "scan_score": 95},
        {"symbol": "READYUSDC", "scan_score": 70},
    ]
    decisions = {
        "WAITUSDC": {
            "decision": "WAIT",
            "context_score": 45,
            "setup_score": 48,
            "risk_score": 55,
            "market_score": 45,
            "confidence": 42,
            "trigger_score": 35,
        },
        "READYUSDC": {
            "decision": "BUY_READY",
            "context_score": 70,
            "setup_score": 75,
            "risk_score": 70,
            "market_score": 60,
            "confidence": 78,
            "trigger_score": 72,
        },
    }

    filtered = filter_constructive_opportunities(
        candidates,
        analyses_by_symbol={"WAITUSDC": {"global_trend": "NEUTRAL"}, "READYUSDC": {"global_trend": "BULLISH"}},
        decisions_by_symbol=decisions,
    )

    assert [item["symbol"] for item in filtered] == ["READYUSDC", "WAITUSDC"]


def test_advanced_triggers_detect_fake_breakout_and_momentum():
    df = pd.DataFrame(
        [
            {"open": 9, "high": 10, "low": 8, "close": 9, "volume": 100, "rsi": 45},
            {"open": 10, "high": 12, "low": 9, "close": 11.2, "volume": 200, "rsi": 55},
            {"open": 11.4, "high": 12.3, "low": 9.8, "close": 9.9, "volume": 240, "rsi": 48},
        ]
    )

    assert detect_fake_breakout(df, resistance=10.5)["detected"] is True

    momentum_df = pd.DataFrame(
        [{"open": i, "high": i + 1, "low": i - 1, "close": i, "volume": 100, "rsi": 50} for i in [10, 10.1, 10.2, 10.25, 10.4, 11.0, 12.1, 13.2]]
    )
    assert detect_momentum_acceleration(momentum_df)["detected"] is True


def test_volatility_squeeze_detection():
    rows = []
    for index in range(25):
        close = 100
        width = 5 if index < 17 else 1
        rows.append({"open": close, "high": close + width, "low": close - width, "close": close, "volume": 100, "rsi": 50})
    assert detect_volatility_squeeze(pd.DataFrame(rows))["detected"] is True


def test_market_regime_and_heatmap_generation():
    results = [
        {"symbol": "BTCUSDC", "global_score": 30, "global_trend": "BEARISH"},
        {"symbol": "ETHUSDC", "global_score": 25, "global_trend": "VERY_BEARISH"},
        {"symbol": "SOLUSDC", "global_score": 40, "global_trend": "BEARISH"},
    ]

    regime = determine_market_regime(results)
    heatmap = generate_market_heatmap(results)

    assert regime["regime"] == "RISK_OFF"
    assert heatmap[0]["symbol"] in {"SOLUSDC", "BTCUSDC", "ETHUSDC"}
    assert any(item["state"] in {"bearish", "weak_bearish"} for item in heatmap)


def test_btc_relative_strength():
    result = calculate_relative_strength({"price_change_pct": 5}, {"price_change_pct": -1})

    assert result["relative_strength"] == "STRONG_OUTPERFORM"
    assert result["spread_pct"] == 6


def test_score_history_acceleration(tmp_path):
    db_path = tmp_path / "history.db"
    for score in [12, 18, 29, 41]:
        save_score_snapshot(
            "BTCUSDC",
            {"context_score": 50, "setup_score": 55, "trigger_score": 20, "risk_score": 60, "market_score": 45, "confidence": 50, "decision": "WAIT"},
            global_score=score,
            db_path=db_path,
        )

    evolution = get_score_evolution("BTCUSDC", db_path=db_path)
    acceleration = calculate_score_acceleration("BTCUSDC", db_path=db_path)

    assert evolution["history"] == [12, 18, 29, 41]
    assert acceleration["trend"] == "IMPROVING"
    assert acceleration["acceleration"] == "STRONG"


def test_backtesting_metrics_and_storage(tmp_path):
    db_path = tmp_path / "history.db"
    save_backtest_result(
        {"symbol": "BTCUSDC", "decision": "BUY_READY", "entry_price": 100, "exit_price": 103, "pnl_pct": 3, "outcome": "TP"},
        db_path=db_path,
    )
    save_backtest_result(
        {"symbol": "ETHUSDC", "decision": "BUY_READY", "entry_price": 100, "exit_price": 98, "pnl_pct": -2, "outcome": "SL"},
        db_path=db_path,
    )

    payload = list_backtest_results(db_path=db_path)
    metrics = calculate_backtest_metrics(payload["results"])

    assert len(payload["results"]) == 2
    assert metrics["winrate"] == 50
    assert metrics["profit_factor"] == 1.5


def test_v5_api_routes(monkeypatch):
    scan_results = [
        {"symbol": "BTCUSDC", "global_score": 62, "global_trend": "BULLISH", "decision_engine": {"decision": "WAIT", "confidence": 60}},
        {"symbol": "SUIUSDC", "global_score": 72, "global_trend": "BULLISH", "decision_engine": {"decision": "BUY_WATCH", "confidence": 66}},
    ]

    monkeypatch.setattr(app_module.scanner, "last_scan_results", scan_results)
    monkeypatch.setattr(
        app_module.scanner,
        "get_cached_scan",
        lambda **kwargs: {"results": scan_results},
    )
    monkeypatch.setattr(
        app_module,
        "get_market_candidates",
        lambda **kwargs: [{"symbol": "SUIUSDC", "scan_score": 82, "reason": "Volume x2.4 avec breakout resistance."}],
    )
    monkeypatch.setattr(
        app_module,
        "list_backtest_results",
        lambda limit=100: {"results": [], "metrics": {"total": 0}},
    )
    monkeypatch.setattr(
        app_module,
        "api_market",
        lambda force=False: {
            "results": scan_results,
            "updated_at": "2026-05-19T10:00:00",
            "source": "test",
        },
    )

    assert client.get("/api/ai-alerts").status_code == 200
    assert client.get("/api/opportunity-scanner").status_code == 404
    assert client.get("/api/market-regime").status_code == 404
    assert client.get("/api/market-heatmap").status_code == 404
    assert client.get("/api/relative-strength/SUIUSDC").status_code == 200
    assert client.get("/api/score-history/BTCUSDC").status_code == 200
    assert client.get("/api/backtesting/results").json()["metrics"]["total"] == 0
