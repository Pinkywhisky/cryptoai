from services.decision_engine import build_decision


def _analysis(**overrides):
    base = {
        "symbol": "ETHUSDC",
        "global_trend": "BULLISH",
        "current_price": 100.0,
        "support": 96.0,
        "resistance": 110.0,
        "distance_to_support_pct": 4.0,
        "distance_to_resistance_pct": 10.0,
        "context_score": 65,
        "setup_score": 70,
        "trigger_score": 65,
        "risk_score": 65,
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
        "triggers": {"score": 65, "label": "Trigger confirme"},
    }
    base.update(overrides)
    return base


def _market(**overrides):
    base = {
        "btc_trend": "NEUTRAL",
        "watchlist_bullish_pct": 50,
        "watchlist_bearish_pct": 20,
        "market_score": 55,
        "message": "Neutre",
    }
    base.update(overrides)
    return base


def test_buy_ready_impossible_if_trigger_score_below_60():
    decision = build_decision(
        "ETHUSDC",
        _analysis(trigger_score=59),
        market_context=_market(),
    )

    assert decision["decision"] != "BUY_READY"
    assert "Trigger absent" in decision["blocking_factors"]


def test_buy_ready_possible_when_context_setup_trigger_risk_are_ok():
    decision = build_decision("ETHUSDC", _analysis(), market_context=_market())

    assert decision["decision"] == "BUY_READY"
    assert decision["decision_label"] == "Achat potentiel"


def test_buy_watch_when_setup_good_but_trigger_is_partial():
    decision = build_decision(
        "ETHUSDC",
        _analysis(trigger_score=45),
        market_context=_market(),
    )

    assert decision["decision"] == "BUY_WATCH"


def test_avoid_when_trend_is_very_bearish():
    decision = build_decision(
        "ETHUSDC",
        _analysis(global_trend="VERY_BEARISH", context_score=30, trigger_score=80),
        market_context=_market(),
    )

    assert decision["decision"] == "AVOID"
    assert "Trend global VERY_BEARISH" in decision["blocking_factors"]


def test_altcoin_penalized_when_btc_is_very_bearish():
    decision = build_decision(
        "ETHUSDC",
        _analysis(),
        market_context=_market(btc_trend="VERY_BEARISH", market_score=25),
    )

    assert decision["decision"] != "BUY_READY"
    assert "BTC trend defavorable" in decision["blocking_factors"]


def test_take_profit_when_position_gain_and_resistance_near():
    decision = build_decision(
        "ETHUSDC",
        _analysis(distance_to_resistance_pct=0.8),
        positions=[{"symbol": "ETHUSDC", "status": "ACTIVE", "gain_loss_pct": 3.1}],
        market_context=_market(),
    )

    assert decision["decision"] == "TAKE_PROFIT"


def test_cut_loss_when_loss_exceeds_max_and_support_breaks():
    decision = build_decision(
        "ETHUSDC",
        _analysis(
            current_price=94.0,
            support=96.0,
            global_trend="BEARISH",
            context_score=38,
        ),
        positions=[
            {
                "symbol": "ETHUSDC",
                "status": "ACTIVE",
                "gain_loss_pct": -4.2,
                "max_loss_accepted_pct": 3,
            }
        ],
        market_context=_market(),
    )

    assert decision["decision"] == "CUT_LOSS"


def test_confidence_low_when_signals_are_contradictory():
    decision = build_decision(
        "ETHUSDC",
        _analysis(context_score=30, setup_score=90, trigger_score=10, risk_score=85),
        market_context=_market(market_score=30),
    )

    assert decision["confidence"] < 40
    assert decision["confidence_label"] == "Faible"


def test_confidence_high_when_signals_are_aligned():
    decision = build_decision(
        "ETHUSDC",
        _analysis(context_score=75, setup_score=80, trigger_score=80, risk_score=75),
        market_context=_market(market_score=70),
    )

    assert decision["confidence"] >= 70
    assert decision["confidence_label"] == "Forte"
