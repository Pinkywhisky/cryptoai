from services.alerts_engine import generate_ai_alerts


def test_ai_alerts_prioritize_risk_then_opportunity_then_watch_then_market():
    rows = [
        {
            "symbol": "RISKUSDC",
            "global_score": 20,
            "global_trend": "BEARISH",
            "decision_engine": {
                "decision": "CUT_LOSS",
                "decision_label": "Réduction du risque possible",
                "confidence": 80,
                "blocking_factors": ["Cassure support"],
            },
        },
        {
            "symbol": "BUYUSDC",
            "global_score": 70,
            "global_trend": "BULLISH",
            "decision_engine": {
                "decision": "BUY_READY",
                "decision_label": "Achat potentiel",
                "confidence": 75,
                "reason_summary": "Trigger confirmé.",
            },
        },
        {
            "symbol": "WATCHUSDC",
            "sources": ["WATCHLIST"],
            "global_score": 48,
            "global_trend": "NEUTRAL",
            "decision_engine": {
                "decision": "WAIT",
                "confidence": 50,
                "setup_score": 52,
                "trigger_score": 30,
                "reason_summary": "Setup en construction.",
            },
        },
    ]

    alerts = generate_ai_alerts(rows)

    assert [alert["type"] for alert in alerts[:3]] == ["RISK", "OPPORTUNITY", "WATCH"]
    assert alerts[0]["severity"] == "HIGH"
    assert all(alert["severity"] != "LOW" for alert in alerts)


def test_alpha_alerts_flag_large_unrealized_loss():
    alerts = generate_ai_alerts(
        [
            {
                "symbol": "CKP",
                "position_info": {
                    "market_source": "BINANCE_ALPHA",
                    "unrealized_pnl_pct": -24.0,
                },
            }
        ]
    )

    assert alerts[0]["type"] == "RISK"
    assert alerts[0]["severity"] == "HIGH"
    assert "Alpha" in alerts[0]["title"]
