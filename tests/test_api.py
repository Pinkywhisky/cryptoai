import pandas as pd
from fastapi.testclient import TestClient

import services.scanner as scanner
import main as app_module
from main import app


client = TestClient(app)


def _market_df(symbol: str = "BTCUSDC") -> pd.DataFrame:
    base_price = 100.0 if symbol != "DOGEUSDC" else 1.0
    return pd.DataFrame(
        {
            "open": [base_price + index for index in range(120)],
            "high": [base_price + index + 1 for index in range(120)],
            "low": [base_price + index - 1 for index in range(120)],
            "close": [base_price + index for index in range(120)],
            "volume": [100.0 + index for index in range(120)],
            "taker_buy_base_volume": [65.0 + index * 0.6 for index in range(120)],
        }
    )


def test_home_route() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Crypto AI Assistant V5" in response.text


def test_api_watchlist_route() -> None:
    response = client.get("/api/watchlist")

    assert response.status_code == 200
    assert response.json()["watchlist"] == scanner.WATCHLIST


def test_analyze_route_with_mocked_binance_client(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        assert symbol == "BTCUSDC"
        assert interval == "1h"
        assert limit == 120
        return _market_df(symbol)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    response = client.get("/api/analyze/BTCUSDC?interval=1h")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDC"
    assert payload["interval"] == "1h"
    assert "score" in payload["analysis"]
    assert "patterns" in payload["analysis"]
    assert "risk_reward" in payload["analysis"]
    assert "momentum" in payload["analysis"]
    assert payload["analysis"]["reasons"]


def test_analyze_multi_route_with_mocked_binance_client(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        assert interval in scanner.TIMEFRAME_WEIGHTS
        return _market_df(symbol)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    response = client.get("/api/analyze-multi/BTCUSDC")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDC"
    assert "global_score" in payload
    assert "setup_quality" in payload
    assert "risk_reward" in payload
    assert "global_trend" in payload
    assert "trend_strength" in payload
    assert set(payload["timeframes"]) == {"15m", "1h", "4h", "1d"}


def test_api_scan_route_with_mocked_binance_client(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if symbol == "MORPHOUSDC":
            raise RuntimeError("symbol not found")
        return _market_df(symbol)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    response = client.get("/api/scan?force=true")

    assert response.status_code == 200
    payload = response.json()
    results = payload["results"]
    assert len(results) == len(scanner.WATCHLIST)
    assert all("summary_reasons" in item for item in results if "global_score" in item)
    assert any(item["symbol"] == "MORPHOUSDC" and "error" in item for item in results)
    assert payload["last_scan_timestamp"]
    assert payload["next_refresh_at"]

    scored_items = [item for item in results if "global_score" in item]
    assert scored_items == sorted(
        scored_items,
        key=lambda item: item["global_score"],
        reverse=True,
    )


def test_api_market_unifies_positions_watch_candidates_and_opportunities(monkeypatch) -> None:
    positions = [
        {
            "symbol": "BTCUSDC",
            "source": "BINANCE",
            "status": "ACTIVE",
            "is_active": 1,
            "current_value": 120,
            "quantity": 0.01,
            "average_buy_price": 78000,
            "profit_loss_pct": 1.2,
            "unrealized_pnl": 9.5,
        },
        {
            "symbol": "DOGEUSDC",
            "source": "BINANCE",
            "status": "ACTIVE",
            "is_active": 1,
            "current_value": 0.01,
        },
    ]
    watch_candidates = [
        {
            "id": 3,
            "symbol": "XRPUSDC",
            "priority": "HIGH",
            "target_buy_price": 1.2,
            "distance_to_target_buy_pct": 2.5,
            "reason": "Support proche",
        }
    ]
    opportunities = [{"symbol": "SUIUSDC", "scan_score": 82, "reason": "Volume x2"}]

    def fake_analysis(symbol: str) -> dict:
        return {
            "symbol": symbol,
            "global_score": 55,
            "global_signal": "ATTENDRE",
            "global_trend": "NEUTRAL",
            "trend_strength": {"trend": "NEUTRAL", "score": 50},
            "current_price": 100,
            "setup_quality": "MOYEN",
            "risk_reward": {"ratio": 1.8, "quality": "MOYEN"},
            "distance_to_support_pct": 2,
            "distance_to_resistance_pct": 5,
            "timeframes": {
                "15m": {"score": 50},
                "1h": {"score": 55},
                "4h": {"score": 52},
                "1d": {"score": 54},
            },
            "momentum": {"medium": {"direction": "NEUTRAL"}},
            "patterns": {},
            "triggers": {"score": 35, "label": "A surveiller"},
        }

    def fake_scan_watchlist_multi(watchlist, limit=120, save_history=True):
        assert "DOGEUSDC" not in watchlist
        assert limit == 120
        assert save_history is True
        return [fake_analysis(symbol) for symbol in watchlist]

    monkeypatch.setattr(app_module, "list_positions", lambda: positions)
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: watch_candidates)
    monkeypatch.setattr(app_module, "get_market_candidates", lambda top_n=30: opportunities)
    monkeypatch.setattr(app_module.scanner, "WATCHLIST", ["BTCUSDC", "ETHUSDC"])
    monkeypatch.setattr(app_module.scanner, "scan_watchlist_multi", fake_scan_watchlist_multi)
    monkeypatch.setattr(app_module, "save_score_snapshot", lambda *args, **kwargs: None)

    response = client.get("/api/market?force=true")

    assert response.status_code == 200
    payload = response.json()
    symbols = [item["symbol"] for item in payload["results"]]
    assert symbols.count("BTCUSDC") == 1
    assert "XRPUSDC" in symbols
    assert "SUIUSDC" in symbols
    assert "ETHUSDC" in symbols
    assert "DOGEUSDC" not in symbols
    assert payload["counts"]["positions"] == 1
    assert payload["counts"]["watch_candidates"] == 1
    assert payload["counts"]["opportunities"] == 1

    btc = next(item for item in payload["results"] if item["symbol"] == "BTCUSDC")
    assert btc["badges"] == ["Détenue", "Watchlist", "Spot"]
    assert btc["position_info"]["unrealized_pnl_pct"] == 1.2
    assert btc["decision_engine"]["decision"] in {"WAIT", "SELL_WATCH", "BUY_WATCH"}

    xrp = next(item for item in payload["results"] if item["symbol"] == "XRPUSDC")
    assert xrp["watch_info"]["id"] == 3
    assert "Surveillance" in xrp["badges"]

    sui = next(item for item in payload["results"] if item["symbol"] == "SUIUSDC")
    assert sui["opportunity_info"]["scan_score"] == 82
    assert "Opportunité" in sui["badges"]


def test_api_market_keeps_manual_invalid_position_visible(monkeypatch) -> None:
    positions = [
        {
            "symbol": "CKP",
            "source": "MANUAL",
            "status": "MANUAL",
            "is_active": 1,
            "quantity": 12,
            "average_buy_price": 1.5,
            "invested_amount": 18,
            "is_valid_symbol": False,
            "analysis_available": False,
            "market_error": "Paire Binance introuvable",
        }
    ]

    def fake_scan_watchlist_multi(watchlist, limit=120, save_history=True):
        assert "CKP" not in watchlist
        return []

    monkeypatch.setattr(app_module, "list_positions", lambda: positions)
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: [])
    monkeypatch.setattr(app_module, "get_market_candidates", lambda top_n=30: [])
    monkeypatch.setattr(app_module.scanner, "WATCHLIST", [])
    monkeypatch.setattr(app_module.scanner, "scan_watchlist_multi", fake_scan_watchlist_multi)

    response = client.get("/api/market?force=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["positions"] == 1
    assert payload["results"][0]["symbol"] == "CKP"
    assert payload["results"][0]["badges"] == ["Détenue", "Manuel"]
    assert payload["results"][0]["error"] == "Paire Binance introuvable"
    assert payload["results"][0]["position_info"]["analysis_available"] is False


def test_api_market_keeps_alpha_watch_candidate_without_spot_scan(monkeypatch) -> None:
    watch_candidates = [
        {
            "id": 4,
            "symbol": "CKP",
            "priority": "HIGH",
            "market_source": "BINANCE_ALPHA",
            "current_price": 2,
            "analysis_available": False,
            "global_score": 42,
            "global_trend": "BEARISH",
            "setup_quality": "LIMITED",
            "price_change_pct": -4.5,
            "quote_volume": 433000,
            "market_error": "Analyse Alpha limitée",
        }
    ]

    def fake_scan_watchlist_multi(watchlist, limit=120, save_history=True):
        assert "CKP" not in watchlist
        return []

    monkeypatch.setattr(app_module, "list_positions", lambda: [])
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: watch_candidates)
    monkeypatch.setattr(app_module, "get_market_candidates", lambda top_n=30: [])
    monkeypatch.setattr(app_module.scanner, "WATCHLIST", [])
    monkeypatch.setattr(app_module.scanner, "scan_watchlist_multi", fake_scan_watchlist_multi)

    response = client.get("/api/market?force=true")

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["symbol"] == "CKP"
    assert row["badges"] == ["Surveillance", "Alpha"]
    assert row["watch_info"]["market_source"] == "BINANCE_ALPHA"
    assert row["current_price"] == 2
    assert "error" not in row
    assert row["global_trend"] == "BEARISH"
    assert row["decision_engine"]["decision_label"] == "Surveillance Alpha"
    assert row["price_change_pct"] == -4.5


def test_build_spot_market_row_keeps_decision_and_price() -> None:
    row = app_module.build_spot_market_row(
        {"symbol": "MORPHOUSDC", "sources": ["WATCHLIST"], "badges": ["Watchlist", "Spot"]},
        {
            "symbol": "MORPHOUSDC",
            "current_price": 1.23,
            "global_score": 54,
            "global_trend": "NEUTRAL",
        },
        {
            "decision": "WAIT",
            "confidence": 58,
            "trigger_label": "Absent",
            "blocking_factors": ["Trigger absent"],
        },
    )

    assert row["symbol"] == "MORPHOUSDC"
    assert row["current_price"] == 1.23
    assert row["trend"] == "NEUTRAL"
    assert row["blocking_factor"] == "Trigger absent"


def test_build_alpha_market_row_uses_partial_analysis_without_unknown() -> None:
    row = app_module.build_alpha_market_row(
        {
            "symbol": "CKP",
            "sources": ["WATCH_CANDIDATE", "BINANCE_ALPHA"],
            "badges": ["Surveillance", "Alpha"],
            "watch_info": {
                "market_source": "BINANCE_ALPHA",
                "current_price": 1.01,
                "global_score": 44,
                "global_trend": "BEARISH",
                "setup_quality": "LIMITED",
                "price_change_pct": -3.2,
            },
        }
    )

    assert row["current_price"] == 1.01
    assert row["trend"] == "BEARISH"
    assert row["blocking_factor"] == "Analyse Alpha partielle"
    assert row["decision_engine"]["decision_label"] == "Surveillance Alpha"
    assert row["trend"] != "UNKNOWN"


def test_api_market_filters_unqualified_automatic_opportunity(monkeypatch) -> None:
    bad_opportunity = [{"symbol": "BADUSDC", "scan_score": 95, "reason": "Volume fort"}]

    def fake_scan_watchlist_multi(watchlist, limit=120, save_history=True):
        return [
            {
                "symbol": "BADUSDC",
                "global_score": 18,
                "global_signal": "EVITER",
                "global_trend": "VERY_BEARISH",
                "trend_strength": {"trend": "VERY_BEARISH", "score": 10},
                "current_price": 1,
                "setup_quality": "MAUVAIS",
                "context_score": 20,
                "setup_score": 25,
                "risk_score": 25,
                "market_score": 30,
                "trigger_score": 0,
                "risk_reward": {"ratio": 0.4, "quality": "MAUVAIS"},
                "timeframes": {},
                "momentum": {},
                "patterns": {},
            }
        ]

    monkeypatch.setattr(app_module, "list_positions", lambda: [])
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: [])
    monkeypatch.setattr(app_module, "get_market_candidates", lambda top_n=30: bad_opportunity)
    monkeypatch.setattr(app_module.scanner, "WATCHLIST", [])
    monkeypatch.setattr(app_module.scanner, "scan_watchlist_multi", fake_scan_watchlist_multi)
    monkeypatch.setattr(app_module, "save_score_snapshot", lambda *args, **kwargs: None)

    response = client.get("/api/market?force=true")

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["counts"]["opportunities"] == 0


def test_api_market_returns_persistent_cache_without_scan(monkeypatch) -> None:
    cached_payload = {
        "results": [{"symbol": "BTCUSDC", "badges": ["Watchlist"], "sources": ["WATCHLIST"]}],
        "universe": [{"symbol": "BTCUSDC", "badges": ["Watchlist"], "sources": ["WATCHLIST"]}],
        "counts": {"positions": 0, "watch_candidates": 0, "opportunities": 0, "total": 1},
        "updated_at": "2026-05-19T00:00:00+00:00",
        "source": "cache",
        "is_stale": False,
    }
    monkeypatch.setattr(app_module, "get_market_payload_cache", lambda: cached_payload)

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("scan should not run when market cache is available")

    monkeypatch.setattr(app_module.scanner, "scan_watchlist_multi", unexpected_scan)

    response = client.get("/api/market")

    assert response.status_code == 200
    assert response.json()["source"] == "cache"
    assert response.json()["results"][0]["symbol"] == "BTCUSDC"


def test_api_ai_alerts_uses_market_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "api_market",
        lambda force=False: {
            "results": [
                {
                    "symbol": "BTCUSDC",
                    "global_score": 72,
                    "global_trend": "BULLISH",
                    "decision_engine": {
                        "decision": "BUY_READY",
                        "decision_label": "Achat potentiel",
                        "confidence": 74,
                        "reason_summary": "Breakout confirmé.",
                    },
                }
            ],
            "updated_at": "2026-05-19T00:00:00+00:00",
            "source": "cache",
        },
    )

    response = client.get("/api/ai-alerts")

    assert response.status_code == 200
    payload = response.json()
    assert payload["alerts"][0]["type"] == "OPPORTUNITY"
    assert payload["alerts"][0]["symbol"] == "BTCUSDC"


def test_dashboard_has_tooltips() -> None:
    response = client.get("/")

    assert 'data-tooltip="Crypto analysée' in response.text
    assert 'data-tooltip="Score sur 100' in response.text
    assert 'data-tooltip="Prix moyen' in response.text
    assert 'id="positionsModal"' in response.text
    assert 'id="watchModal"' in response.text
    assert 'id="positionsModalButton"' not in response.text
    assert 'id="watchModalButton"' not in response.text
    assert 'id="mobilePositionsButton"' not in response.text
    assert 'id="mobileWatchButton"' not in response.text
    assert 'id="journalModal"' in response.text
    assert 'id="symbolInput"' not in response.text
    assert 'id="quickWatchForm"' in response.text
    assert "Surveiller une crypto" in response.text
    assert 'id="portfolioSummary"' in response.text
    assert "Actualiser" in response.text
    assert "Gestion" in response.text
    assert "Trend global" in response.text
    assert 'aria-haspopup="true"' in response.text
    assert "Paramètres API" not in response.text


def test_api_history_route(monkeypatch) -> None:
    def fake_get_symbol_history(symbol: str, limit: int = 50):
        return [{"timestamp": "2026-01-01T00:00:00+00:00", "score": 42, "price": 78000.0}]

    monkeypatch.setattr(app_module, "get_symbol_history", fake_get_symbol_history)

    response = client.get("/api/history/BTCUSDC")

    assert response.status_code == 200
    assert response.json()[0]["score"] == 42


def test_api_top_momentum_route_with_mocked_binance_client(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        return _market_df(symbol)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    response = client.get("/api/top-momentum")

    assert response.status_code == 200
    assert len(response.json()) <= 5


def test_api_top_setups_route_with_mocked_binance_client(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        return _market_df(symbol)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    response = client.get("/api/top-setups")

    assert response.status_code == 200
    assert len(response.json()) <= 5


def test_positions_api_routes(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "list_positions", lambda: [{"id": 1, "symbol": "BTCUSDC"}])
    monkeypatch.setattr(app_module, "create_position", lambda payload: {"id": 1, **payload})
    monkeypatch.setattr(app_module, "update_position", lambda position_id, payload: {"id": position_id, **payload})
    monkeypatch.setattr(app_module, "delete_position", lambda position_id: True)

    assert client.get("/api/positions").json()[0]["symbol"] == "BTCUSDC"
    assert client.post("/api/positions", json={"symbol": "BTCUSDC"}).json()["id"] == 1
    assert client.put("/api/positions/1", json={"note": "ok"}).json()["note"] == "ok"
    assert client.delete("/api/positions/1").json()["deleted"] is True


def test_watch_candidates_api_routes(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: [{"id": 1, "symbol": "ETHUSDC"}])
    monkeypatch.setattr(app_module, "create_watch_candidate", lambda payload: {"id": 1, **payload})
    monkeypatch.setattr(app_module, "update_watch_candidate", lambda candidate_id, payload: {"id": candidate_id, **payload})
    monkeypatch.setattr(app_module, "delete_watch_candidate", lambda candidate_id: True)

    assert client.get("/api/watch-candidates").json()[0]["symbol"] == "ETHUSDC"
    assert client.post("/api/watch-candidates", json={"symbol": "ETHUSDC"}).json()["id"] == 1
    assert client.put("/api/watch-candidates/1", json={"priority": "HIGH"}).json()["priority"] == "HIGH"
    assert client.delete("/api/watch-candidates/1").json()["deleted"] is True


def test_watch_candidates_api_errors_are_json(monkeypatch) -> None:
    def duplicate(payload):
        raise ValueError("Symbol already exists")

    monkeypatch.setattr(app_module, "create_watch_candidate", duplicate)
    response = client.post("/api/watch-candidates", json={"symbol": "ETHUSDC"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"success": False, "error": "Symbol already exists"}

    monkeypatch.setattr(app_module, "delete_watch_candidate", lambda candidate_id: False)
    delete_response = client.delete("/api/watch-candidates/999")
    assert delete_response.status_code == 404
    assert delete_response.headers["content-type"].startswith("application/json")
    assert delete_response.json()["success"] is False


def test_invalid_symbol_api_routes_do_not_return_500(monkeypatch) -> None:
    invalid_item = {
        "id": 1,
        "symbol": "CKP",
        "is_valid_symbol": False,
        "analysis_available": False,
        "market_error": "Symbole Binance invalide",
    }
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: [invalid_item])
    monkeypatch.setattr(app_module, "list_positions", lambda: [invalid_item])
    monkeypatch.setattr(app_module, "generate_all_alerts", lambda positions, candidates: [])

    for path in ["/api/watch-candidates", "/api/positions", "/api/alerts"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")


def test_invalid_symbol_create_routes_return_json(monkeypatch) -> None:
    def invalid(payload):
        raise ValueError("Symbole invalide ou paire Binance introuvable : CKP")

    monkeypatch.setattr(app_module, "create_watch_candidate", invalid)
    monkeypatch.setattr(app_module, "create_position", invalid)

    watch_response = client.post("/api/watch-candidates", json={"symbol": "CKP"})
    position_response = client.post("/api/positions", json={"symbol": "CKP"})

    assert watch_response.status_code == 400
    assert position_response.status_code == 400
    assert watch_response.json()["success"] is False
    assert position_response.json()["error"].startswith("Symbole invalide")


def test_cleanup_invalid_symbols_route(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "cleanup_invalid_symbols",
        lambda: {"removed": 1, "invalid_symbols": ["CKP"]},
    )

    response = client.post("/api/admin/cleanup-invalid-symbols")

    assert response.status_code == 200
    assert response.json()["removed"] == 1
    assert response.json()["invalid_symbols"] == ["CKP"]


def test_repair_market_sources_route(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "repair_market_sources", lambda: {"updated": 1, "symbols": ["CKP"]})

    response = client.post("/api/admin/repair-market-sources")

    assert response.status_code == 200
    assert response.json() == {"updated": 1, "symbols": ["CKP"]}


def test_journal_and_alerts_api_routes(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "list_journal",
        lambda: [{"id": 1, "symbol": "SOLUSDC", "result_status": "WIN", "pnl_percent": 2}],
    )
    monkeypatch.setattr(app_module, "create_journal_entry", lambda payload: {"id": 1, **payload})
    monkeypatch.setattr(app_module, "update_journal_entry", lambda entry_id, payload: {"id": entry_id, **payload})
    monkeypatch.setattr(app_module, "delete_journal_entry", lambda entry_id: True)
    monkeypatch.setattr(app_module, "list_positions", lambda: [{"symbol": "BTCUSDC"}])
    monkeypatch.setattr(app_module, "list_watch_candidates", lambda: [])
    monkeypatch.setattr(app_module, "generate_all_alerts", lambda positions, candidates: [{"symbol": "BTCUSDC", "severity": "HIGH"}])

    assert client.get("/api/journal").json()[0]["symbol"] == "SOLUSDC"
    assert client.post("/api/journal", json={"symbol": "SOLUSDC", "action": "ATTENTE"}).json()["id"] == 1
    assert client.put("/api/journal/1", json={"result_status": "WIN"}).json()["result_status"] == "WIN"
    assert client.get("/api/journal/stats").json()["closed_trades"] == 1
    assert client.get("/api/journal/analytics").json()["winrate"]["winrate"] == 100
    assert client.delete("/api/journal/1").json()["deleted"] is True
    assert client.get("/api/alerts").json()[0]["severity"] == "HIGH"


def test_binance_api_routes(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "get_binance_status", lambda: {"configured": False, "read_only_mode": True, "last_sync": None})
    monkeypatch.setattr(app_module, "sync_binance_account", lambda: {"synced": False, "message": "API Binance privée non configurée"})
    monkeypatch.setattr(app_module, "get_balances", lambda: [{"asset": "BTC", "total": 0.1}])
    monkeypatch.setattr(app_module, "get_stored_trades", lambda symbol: [{"symbol": symbol, "trade_id": 1}])
    monkeypatch.setattr(app_module, "get_binance_positions", lambda: [{"symbol": "BTCUSDC"}])
    monkeypatch.setattr(app_module, "get_pnl_summary", lambda: {"total_invested": 10, "current_value": 11})
    monkeypatch.setattr(app_module, "get_account_summary", lambda: {"active_value_usdc": 11, "dust_count": 1})
    monkeypatch.setattr(app_module, "get_closed_trade_symbols", lambda: [{"symbol": "DOGEUSDC"}])
    monkeypatch.setattr(app_module, "get_dust_balances", lambda: [{"asset": "DOGE"}])
    monkeypatch.setattr(app_module, "get_active_positions", lambda: [{"symbol": "BTCUSDC"}])
    monkeypatch.setattr(app_module, "get_inactive_positions", lambda: [{"symbol": "DOGEUSDC"}])

    assert client.get("/api/binance/status").json()["read_only_mode"] is True
    assert client.post("/api/binance/sync").json()["synced"] is False
    assert client.get("/api/binance/balances").json()[0]["asset"] == "BTC"
    assert client.get("/api/binance/trades/BTCUSDC").json()[0]["trade_id"] == 1
    assert client.get("/api/binance/positions").json()[0]["symbol"] == "BTCUSDC"
    assert client.get("/api/binance/pnl").json()["total_invested"] == 10
    assert client.get("/api/binance/account-summary").json()["dust_count"] == 1
    assert client.get("/api/binance/closed-trades").json()[0]["symbol"] == "DOGEUSDC"
    assert client.get("/api/binance/dust").json()[0]["asset"] == "DOGE"
    assert client.get("/api/positions/active").json()[0]["symbol"] == "BTCUSDC"
    assert client.get("/api/positions/inactive").json()[0]["symbol"] == "DOGEUSDC"


def test_admin_reset_route_clears_database_and_backend_cache(monkeypatch) -> None:
    def fake_reset_database():
        return {"positions": {"existed": True, "deleted": 2}}

    def fake_reset_scan_cache(reset_watchlist=False):
        return {
            "last_scan_results": None,
            "last_scan_timestamp": None,
            "scan_in_progress": False,
            "watchlist": [] if reset_watchlist else scanner.DEFAULT_WATCHLIST,
            "reset_watchlist": reset_watchlist,
        }

    monkeypatch.setattr(app_module, "reset_database", fake_reset_database)
    monkeypatch.setattr(app_module.scanner, "reset_scan_cache", fake_reset_scan_cache)

    response = client.post("/api/admin/reset?reset_watchlist=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tables"]["positions"]["deleted"] == 2
    assert payload["cache"]["scan_in_progress"] is False
    assert payload["cache"]["sync_in_progress"] is False
    assert payload["cache"]["watchlist"] == []


def test_dashboard_js_contains_badges_and_binance_sync() -> None:
    with open("static/app.js", encoding="utf-8") as file:
        content = file.read()

    assert "Détenue" in content
    assert "DÃ" not in content
    assert "symbol-position-line" in content
    assert "detail-main-grid" in content
    assert "Surveillance" in content
    assert "/api/binance/sync" in content
    assert "position.is_active" in content
    assert "position.status === \"ACTIVE\"" in content
    assert "MIN_ACTIVE_POSITION_VALUE_USDC" in content
    assert "clearFrontendCache" in content
    assert "response.headers.get(\"content-type\")" in content
    assert "response.text()" in content
    assert "Réponse API vide" in content
    assert "Impossible d'ajouter la crypto" in content
    assert "Symbole invalide. Exemple attendu : BTCUSDC ou BTC." in content
    assert "data-delete-watch" in content
    assert "quickWatchForm" in content
    assert "/api/market?force=" in content
    assert "data-market-remove-watch" in content
    assert "data-market-delete-position" in content
    assert "Supprimer position" in content
    assert "Position manuelle supprimée" in content
    assert "refreshMarketCockpit" in content
    assert "initDashboard" in content
    assert "setButtonLoading" in content
    assert "is-loading" in content
    assert "Aucune donnée marché disponible" in content
    assert "document.querySelector(`#${buttonId}`)?.addEventListener" in content
    assert "Retirer" in content
    assert "loadMarket(true)" in content
    assert "/api/admin/reset" in content
    assert "closeActionMenus" in content
    assert "event.key === \"Escape\"" in content
    assert "trendClass" in content
    assert "global_trend" in content
    assert "Valeur portefeuille" in content
    assert "PnL latent" in content
    assert "PnL réalisé" in content
    assert "PnL total" in content
    assert "Positions encore ouvertes" in content
    assert "Trades déjà clôturés" in content
    assert "/api/ai-alerts" in content
    assert "renderAiAlerts" in content
    assert "opportunitiesList" not in content
    assert "risksList" not in content
    assert "marketHeatmap" not in content


def test_dashboard_template_uses_single_ai_alerts_section() -> None:
    response = client.get("/")
    html = response.text

    assert "Alertes IA" in html
    assert 'id="aiAlertsList"' in html
    assert "Opportunites" not in html
    assert "Risques sur mes positions" not in html
    assert "Scanner opportunités" not in html
    assert "Heatmap" not in html


def test_dropdown_css_is_click_based_and_stable() -> None:
    with open("static/style.css", encoding="utf-8") as file:
        content = file.read()

    assert ".action-menu.open .action-menu-list" in content
    assert ".action-menu:hover .action-menu-list" not in content
    assert "z-index: 500" in content
    assert "pointer-events: auto" in content


def test_tooltip_css_uses_above_headers() -> None:
    with open("static/style.css", encoding="utf-8") as file:
        content = file.read()

    assert "bottom: calc(100% + 6px)" in content
