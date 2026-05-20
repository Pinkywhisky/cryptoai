from fastapi.testclient import TestClient

import main as app_module
from main import app


client = TestClient(app)


def _scan_item(symbol="BTCUSDC", decision_scores=None):
    decision_scores = decision_scores or {}
    return {
        "symbol": symbol,
        "global_score": 72,
        "global_signal": "ACHAT POTENTIEL",
        "global_trend": "BULLISH",
        "current_price": 100.0,
        "support": 96.0,
        "resistance": 110.0,
        "distance_to_support_pct": 4.0,
        "distance_to_resistance_pct": 10.0,
        "setup_quality": "BON",
        "risk_reward": {"ratio": 2.5, "quality": "BON"},
        "momentum": {
            "medium": {"direction": "BULLISH", "strength": "MEDIUM"},
            "volume_acceleration": {"detected": True, "ratio": 1.8},
        },
        "patterns": {
            "upper_wick": {"detected": False},
            "buy_pressure": {"detected": True},
            "consolidation": {"detected": False},
        },
        "triggers": {"score": 70, "label": "Trigger confirme"},
        "context_score": decision_scores.get("context_score", 70),
        "setup_score": decision_scores.get("setup_score", 70),
        "trigger_score": decision_scores.get("trigger_score", 70),
        "risk_score": decision_scores.get("risk_score", 65),
        "timeframes": {},
    }


def test_api_decision_symbol(monkeypatch):
    monkeypatch.setattr(app_module.scanner, "analyze_symbol_multi", lambda symbol, limit=120: _scan_item(symbol))
    monkeypatch.setattr(app_module, "list_positions", lambda: [])
    app_module.scanner.last_scan_results = [_scan_item("BTCUSDC")]

    response = client.get("/api/decision/BTCUSDC")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDC"
    assert payload["decision"] == "BUY_READY"
    assert "context_score" in payload


def test_api_decisions(monkeypatch):
    results = [_scan_item("BTCUSDC"), _scan_item("ETHUSDC", {"trigger_score": 45})]
    app_module.scanner.last_scan_results = results
    monkeypatch.setattr(
        app_module.scanner,
        "get_cached_scan",
        lambda watchlist, limit, force=False: {"results": results},
    )
    monkeypatch.setattr(app_module, "list_positions", lambda: [])

    response = client.get("/api/decisions")

    assert response.status_code == 200
    assert {item["symbol"] for item in response.json()} == {"BTCUSDC", "ETHUSDC"}


def test_legacy_opportunities_and_risks_routes_removed():
    assert client.get("/api/opportunities").status_code == 404
    assert client.get("/api/risks").status_code == 404
