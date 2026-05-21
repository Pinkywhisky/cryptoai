from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


WIN_STATUSES = {"WIN"}
LOSS_STATUSES = {"LOSS"}
CLOSED_STATUSES = {"WIN", "LOSS", "BREAKEVEN"}


def _closed(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if str(entry.get("result_status") or "").upper() in CLOSED_STATUSES]


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def compute_winrate(entries: list[dict[str, Any]]) -> dict[str, Any]:
    closed = _closed(entries)
    wins = [entry for entry in closed if str(entry.get("result_status") or "").upper() in WIN_STATUSES]
    losses = [entry for entry in closed if str(entry.get("result_status") or "").upper() in LOSS_STATUSES]
    return {
        "total_trades": len(entries),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(len(wins) / len(closed) * 100, 2) if closed else 0,
        "avg_gain": _avg([float(entry.get("pnl_percent")) for entry in wins if entry.get("pnl_percent") is not None]),
        "avg_loss": _avg([float(entry.get("pnl_percent")) for entry in losses if entry.get("pnl_percent") is not None]),
    }


def compute_best_setups(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_setup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in _closed(entries):
        setup = entry.get("ai_setup_quality") or "UNKNOWN"
        by_setup[str(setup).upper()].append(entry)

    ranked = []
    for setup, setup_entries in by_setup.items():
        wins = sum(1 for entry in setup_entries if str(entry.get("result_status") or "").upper() == "WIN")
        pnl_values = [float(entry.get("pnl_percent")) for entry in setup_entries if entry.get("pnl_percent") is not None]
        ranked.append(
            {
                "setup": setup,
                "trades": len(setup_entries),
                "winrate": round(wins / len(setup_entries) * 100, 2),
                "avg_pnl_percent": _avg(pnl_values),
            }
        )
    ranked.sort(key=lambda item: (item["avg_pnl_percent"] or -999, item["winrate"]), reverse=True)
    return {
        "best_setup": ranked[0] if ranked else None,
        "worst_setup": ranked[-1] if ranked else None,
        "setups": ranked,
    }


def compute_emotion_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_emotion: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in _closed(entries):
        emotion = entry.get("emotion") or "UNKNOWN"
        by_emotion[str(emotion).upper()].append(entry)

    stats = []
    for emotion, emotion_entries in by_emotion.items():
        wins = sum(1 for entry in emotion_entries if str(entry.get("result_status") or "").upper() == "WIN")
        pnl_values = [float(entry.get("pnl_percent")) for entry in emotion_entries if entry.get("pnl_percent") is not None]
        stats.append(
            {
                "emotion": emotion,
                "trades": len(emotion_entries),
                "winrate": round(wins / len(emotion_entries) * 100, 2),
                "avg_pnl_percent": _avg(pnl_values),
            }
        )
    stats.sort(key=lambda item: (item["avg_pnl_percent"] or -999, item["winrate"]), reverse=True)
    dangerous = sorted(stats, key=lambda item: (item["avg_pnl_percent"] or 999, item["winrate"]))
    return {
        "emotions": stats,
        "best_emotion": stats[0] if stats else None,
        "riskiest_emotion": dangerous[0] if dangerous else None,
    }


def compute_ai_accuracy(entries: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = []
    correct = 0
    for entry in _closed(entries):
        decision = str(entry.get("ai_snapshot", {}).get("decision", {}).get("decision") or entry.get("decision_type") or "").upper()
        result = str(entry.get("result_status") or "").upper()
        if not decision or result not in {"WIN", "LOSS"}:
            continue
        predicted_positive = decision in {"BUY_READY", "BUY_WATCH", "BUY", "REINFORCE"}
        predicted_defensive = decision in {"WAIT", "AVOID", "SELL_WATCH", "CUT_LOSS", "SELL"}
        is_correct = (predicted_positive and result == "WIN") or (predicted_defensive and result == "LOSS")
        correct += 1 if is_correct else 0
        evaluated.append(entry)
    return {
        "evaluated": len(evaluated),
        "correct": correct,
        "accuracy": round(correct / len(evaluated) * 100, 2) if evaluated else 0,
    }


def compute_user_patterns(entries: list[dict[str, Any]]) -> list[str]:
    insights: list[str] = []
    emotion_stats = compute_emotion_stats(entries)
    riskiest = emotion_stats.get("riskiest_emotion")
    if riskiest and riskiest.get("trades", 0) >= 2 and (riskiest.get("winrate") or 0) < 50:
        insights.append(
            f"Tes trades avec {riskiest['emotion']} sont gagnants seulement à {riskiest['winrate']}%."
        )

    trade_counts = Counter(str(entry.get("trade_type") or "UNKNOWN").upper() for entry in _closed(entries))
    if trade_counts:
        most_common = trade_counts.most_common(1)[0][0]
        insights.append(f"Ton style le plus fréquent est {most_common}.")

    best_setup = compute_best_setups(entries).get("best_setup")
    if best_setup and best_setup.get("trades", 0) >= 1:
        insights.append(
            f"Tes meilleurs setups actuels : {best_setup['setup']} avec {best_setup['winrate']}% de réussite."
        )
    return insights


def compute_journal_analytics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "winrate": compute_winrate(entries),
        "setups": compute_best_setups(entries),
        "emotions": compute_emotion_stats(entries),
        "ai_accuracy": compute_ai_accuracy(entries),
        "patterns": compute_user_patterns(entries),
    }
