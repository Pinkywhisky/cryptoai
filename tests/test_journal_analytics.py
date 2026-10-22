from copy import deepcopy

import pytest

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


@pytest.mark.parametrize("snapshot", [
    None, [], False, 0, "legacy", {},
    {"decision": None}, {"decision": []}, {"decision": "BUY_READY"}, {"decision": 0},
])
def test_journal_analytics_accepts_legacy_snapshots_without_changing_entries(snapshot):
    entries = [
        {"result_status": "WIN", "decision_type": "BUY", "ai_snapshot": snapshot},
        {"result_status": "LOSS", "decision_type": "WAIT", "ai_snapshot": snapshot},
        {"result_status": "WIN", "ai_snapshot": snapshot},
    ]
    original = deepcopy(entries)

    analytics = compute_journal_analytics(entries)

    assert analytics["ai_accuracy"] == {"evaluated": 2, "correct": 2, "accuracy": 100}
    assert analytics["winrate"]["closed_trades"] == 3
    assert analytics["winrate"]["avg_gain"] is None
    assert entries == original


def test_journal_ai_accuracy_uses_recorded_decision_when_snapshot_is_missing():
    assert compute_ai_accuracy([
        {"result_status": "LOSS", "decision_type": "WAIT"},
        {"result_status": "WIN"},
    ]) == {"evaluated": 1, "correct": 1, "accuracy": 100}


def test_journal_ai_accuracy_preserves_valid_snapshot_precedence():
    assert compute_ai_accuracy([{
        "result_status": "WIN",
        "decision_type": "WAIT",
        "ai_snapshot": {"decision": {"decision": "BUY_READY"}},
    }]) == {"evaluated": 1, "correct": 1, "accuracy": 100}
