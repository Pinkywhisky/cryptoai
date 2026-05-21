from services.journal_analytics import (
    compute_ai_accuracy,
    compute_emotion_stats,
    compute_journal_analytics,
    compute_winrate,
)


def test_journal_winrate_and_emotion_stats():
    entries = [
        {
            "symbol": "BTCUSDC",
            "result_status": "WIN",
            "pnl_percent": 4,
            "emotion": "CALM",
            "ai_setup_quality": "BON",
        },
        {
            "symbol": "ETHUSDC",
            "result_status": "LOSS",
            "pnl_percent": -2,
            "emotion": "FOMO",
            "ai_setup_quality": "MAUVAIS",
        },
        {
            "symbol": "SOLUSDC",
            "result_status": "OPEN",
            "emotion": "CALM",
        },
    ]

    winrate = compute_winrate(entries)
    emotions = compute_emotion_stats(entries)

    assert winrate["closed_trades"] == 2
    assert winrate["winrate"] == 50
    assert winrate["avg_gain"] == 4
    assert winrate["avg_loss"] == -2
    assert emotions["riskiest_emotion"]["emotion"] == "FOMO"


def test_journal_ai_accuracy_and_patterns():
    entries = [
        {
            "result_status": "WIN",
            "decision_type": "BUY",
            "trade_type": "SWING",
            "emotion": "CALM",
            "ai_setup_quality": "BON",
            "ai_snapshot": {"decision": {"decision": "BUY_READY"}},
            "pnl_percent": 2,
        },
        {
            "result_status": "LOSS",
            "decision_type": "WAIT",
            "trade_type": "SCALP",
            "emotion": "FOMO",
            "ai_setup_quality": "MAUVAIS",
            "ai_snapshot": {"decision": {"decision": "AVOID"}},
            "pnl_percent": -3,
        },
    ]

    accuracy = compute_ai_accuracy(entries)
    analytics = compute_journal_analytics(entries)

    assert accuracy["accuracy"] == 100
    assert analytics["setups"]["best_setup"]["setup"] == "BON"
    assert analytics["patterns"]
