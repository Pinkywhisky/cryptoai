from typing import Any

from services.btc_correlation import btc_altcoin_adjustment, calculate_relative_strength
from services.market_context import calculate_market_score
from services.market_regime import determine_market_regime
from services.score_history import calculate_score_acceleration


DECISION_LABELS = {
    "BUY_WATCH": "Surveiller achat",
    "BUY_READY": "Achat potentiel",
    "WAIT": "Attendre",
    "AVOID": "Eviter",
    "SELL_WATCH": "Surveiller vente",
    "TAKE_PROFIT": "Prise de benefice possible",
    "CUT_LOSS": "Reduction du risque possible",
}


def _clamp(value: float | int) -> int:
    return max(0, min(100, int(round(value))))


def _trend_value(trend: str | None) -> int:
    return {
        "VERY_BULLISH": 80,
        "BULLISH": 65,
        "NEUTRAL": 50,
        "BEARISH": 35,
        "VERY_BEARISH": 20,
    }.get(trend or "NEUTRAL", 50)


def _quality_value(quality: str | None) -> int:
    return {
        "EXCELLENT": 82,
        "BON": 68,
        "MOYEN": 52,
        "MAUVAIS": 32,
        "INVALID": 20,
    }.get(quality or "MOYEN", 50)


def _main_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    return analysis.get("timeframes", {}).get("1h") or analysis


def _score_context(analysis: dict[str, Any]) -> int:
    if "context_score" in analysis:
        return _clamp(analysis["context_score"])

    trend_score = analysis.get("trend_strength", {}).get("score")
    if trend_score is not None:
        score = (float(trend_score) + 100) / 2
    else:
        score = _trend_value(analysis.get("global_trend"))

    timeframes = analysis.get("timeframes", {})
    if timeframes:
        tf_scores = [data.get("score", 50) for data in timeframes.values()]
        score = score * 0.45 + (sum(tf_scores) / len(tf_scores)) * 0.55

    main = _main_analysis(analysis)
    momentum = main.get("momentum", {}).get("medium", {})
    volume = main.get("momentum", {}).get("volume_acceleration", {})
    if momentum.get("direction") == "BULLISH":
        score += 7
    elif momentum.get("direction") == "BEARISH":
        score -= 9
    if volume.get("detected"):
        score += 5
    elif volume:
        score -= 3

    return _clamp(score)


def _score_setup(analysis: dict[str, Any]) -> int:
    if "setup_score" in analysis:
        return _clamp(analysis["setup_score"])

    main = _main_analysis(analysis)
    rr = main.get("risk_reward") or analysis.get("risk_reward") or {}
    score = _quality_value(main.get("setup_quality") or analysis.get("setup_quality") or rr.get("quality"))

    support_distance = analysis.get("distance_to_support_pct", main.get("distance_to_support_pct"))
    resistance_distance = analysis.get("distance_to_resistance_pct", main.get("distance_to_resistance_pct"))
    patterns = main.get("patterns", {})
    if support_distance is not None and 0 <= support_distance <= 2:
        score += 10
    if resistance_distance is not None and 0 <= resistance_distance <= 1:
        score -= 12
    elif resistance_distance is not None and resistance_distance > 3:
        score += 5
    if patterns.get("consolidation", {}).get("detected"):
        score += 7

    return _clamp(score)


def _score_trigger(analysis: dict[str, Any]) -> int:
    if "trigger_score" in analysis:
        return _clamp(analysis["trigger_score"])

    triggers = analysis.get("triggers") or _main_analysis(analysis).get("triggers") or {}
    return _clamp(triggers.get("score", 0))


def _score_risk(analysis: dict[str, Any], position: dict[str, Any] | None = None) -> int:
    if "risk_score" in analysis:
        return _clamp(analysis["risk_score"])

    main = _main_analysis(analysis)
    rr = main.get("risk_reward") or analysis.get("risk_reward") or {}
    score = 50 + min(25, float(rr.get("ratio", 0)) * 8)

    support_distance = analysis.get("distance_to_support_pct", main.get("distance_to_support_pct"))
    resistance_distance = analysis.get("distance_to_resistance_pct", main.get("distance_to_resistance_pct"))
    patterns = main.get("patterns", {})
    if support_distance is not None and support_distance > 6:
        score -= 12
    if resistance_distance is not None and 0 <= resistance_distance <= 1:
        score -= 18
    if patterns.get("upper_wick", {}).get("detected"):
        score -= 18
    if patterns.get("sell_pressure", {}).get("detected"):
        score -= 10

    if position:
        stop_distance = position.get("distance_to_stop_loss_pct")
        if stop_distance is not None and abs(float(stop_distance)) <= 1:
            score -= 15

    return _clamp(score)


def _active_position(symbol: str, positions: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for position in positions or []:
        if position.get("symbol") != symbol:
            continue
        if position.get("status", "ACTIVE") in {"ACTIVE", "MANUAL"} and position.get("is_active", 1):
            return position
    return None


def _positive_factors(analysis: dict[str, Any], setup_score: int, trigger_score: int, risk_score: int) -> list[str]:
    main = _main_analysis(analysis)
    factors: list[str] = []
    if risk_score >= 60:
        factors.append("Risk/reward favorable")
    if (analysis.get("distance_to_support_pct", main.get("distance_to_support_pct")) or 99) <= 2:
        factors.append("Support proche")
    if main.get("momentum", {}).get("volume_acceleration", {}).get("detected"):
        factors.append("Volume en acceleration")
    if main.get("patterns", {}).get("buy_pressure", {}).get("detected"):
        factors.append("Pression acheteuse")
    if trigger_score >= 60:
        factors.append("Breakout confirme")
    if main.get("momentum", {}).get("medium", {}).get("direction") == "BULLISH":
        factors.append("Momentum 1h positif")
    if main.get("patterns", {}).get("consolidation", {}).get("detected") and setup_score >= 55:
        factors.append("Consolidation propre")
    return factors[:6]


def _blocking_factors(
    symbol: str,
    analysis: dict[str, Any],
    context_score: int,
    trigger_score: int,
    risk_score: int,
    market_context: dict[str, Any],
) -> tuple[list[str], list[str]]:
    main = _main_analysis(analysis)
    trend = analysis.get("global_trend") or main.get("trend_strength", {}).get("trend")
    momentum = main.get("momentum", {}).get("medium", {})
    factors: list[str] = []
    major: list[str] = []

    if trigger_score < 60:
        factors.append("Trigger absent")
        major.append("Trigger absent")
    if trend == "VERY_BEARISH" or context_score < 35:
        factors.append("Trend global VERY_BEARISH")
        major.append("Trend global VERY_BEARISH")
    elif trend == "BEARISH":
        factors.append("Trend global bearish")
    if risk_score < 35:
        factors.append("Risk/reward mauvais")
        major.append("Risk/reward mauvais")
    if symbol != "BTCUSDC" and market_context.get("btc_trend") == "VERY_BEARISH":
        factors.append("BTC trend defavorable")
        major.append("BTC trend defavorable")
    if main.get("momentum", {}).get("volume_acceleration", {}).get("detected") is False:
        factors.append("Volume insuffisant")

    price = main.get("price") or analysis.get("current_price")
    ma25 = main.get("ma25")
    ma99 = main.get("ma99")
    if price and ma25 and ma99 and price < ma25 and price < ma99 and momentum.get("direction") == "BEARISH":
        factors.append("Prix sous MA25 et MA99")
        major.append("Prix sous MA25 et MA99")
    resistance_distance = analysis.get("distance_to_resistance_pct", main.get("distance_to_resistance_pct"))
    if resistance_distance is not None and 0 <= resistance_distance <= 1:
        factors.append("Proche resistance")
    if main.get("patterns", {}).get("upper_wick", {}).get("detected"):
        factors.append("Meche haute importante")

    return list(dict.fromkeys(factors)), list(dict.fromkeys(major))


def _confidence(
    context_score: int,
    setup_score: int,
    trigger_score: int,
    risk_score: int,
    market_score: int,
    analysis: dict[str, Any],
) -> tuple[int, str]:
    scores = [context_score, setup_score, trigger_score, risk_score, market_score]
    confidence = sum(scores) / len(scores)
    spread = max(scores) - min(scores)
    confidence -= min(25, spread * 0.35)

    timeframes = analysis.get("timeframes", {})
    if timeframes:
        trends = [
            data.get("trend_strength", {}).get("trend")
            for data in timeframes.values()
            if data.get("trend_strength", {}).get("trend")
        ]
        if trends and len(set(trends)) <= 2:
            confidence += 8
        elif trends:
            confidence -= 8

    main = _main_analysis(analysis)
    if main.get("momentum", {}).get("volume_acceleration", {}).get("detected"):
        confidence += 6
    if (setup_score >= 65 and trigger_score < 30) or (context_score < 40 and setup_score >= 65):
        confidence -= 18
    if market_score < 40 and context_score >= 55:
        confidence -= 10

    value = _clamp(confidence)
    if value < 40:
        label = "Faible"
    elif value < 70:
        label = "Moyenne"
    else:
        label = "Forte"
    return value, label


def _summary(decision: str, blocking: list[str], positives: list[str]) -> str:
    if decision == "BUY_READY":
        return "Contexte, setup, trigger et risque sont coherents pour une surveillance active d'achat."
    if decision == "BUY_WATCH":
        return "Setup interessant, mais le trigger d'entree reste partiel."
    if decision == "AVOID":
        return "Contexte fragile et risque trop eleve pour chercher une entree."
    if decision == "TAKE_PROFIT":
        return "Position en gain avec zone de prise de benefice ou resistance a proximite."
    if decision == "CUT_LOSS":
        return "Perte maximale ou cassure technique detectee, reduction du risque a envisager."
    if decision == "SELL_WATCH":
        return "Position detenue avec degradation ou zone de resistance a surveiller."
    if blocking and positives:
        return f"{blocking[0]}, mais {positives[0].lower()}."
    if blocking:
        return f"{blocking[0]}."
    return "Signaux mixtes, attendre une confirmation plus nette."


def build_decision(
    symbol: str,
    analysis: dict[str, Any],
    scan_results: list[dict[str, Any]] | None = None,
    positions: list[dict[str, Any]] | None = None,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = symbol.upper()
    position = _active_position(symbol, positions)
    market_context = market_context or calculate_market_score(symbol, scan_results)
    market_score = _clamp(market_context.get("market_score", 50))
    context_score = _score_context(analysis)
    setup_score = _score_setup(analysis)
    trigger_score = _score_trigger(analysis)
    risk_score = _score_risk(analysis, position)
    blocking, major_blocking = _blocking_factors(
        symbol,
        analysis,
        context_score,
        trigger_score,
        risk_score,
        market_context,
    )
    positives = _positive_factors(analysis, setup_score, trigger_score, risk_score)

    main = _main_analysis(analysis)
    trend = analysis.get("global_trend") or main.get("trend_strength", {}).get("trend")
    resistance_distance = analysis.get("distance_to_resistance_pct", main.get("distance_to_resistance_pct"))
    upper_wick = main.get("patterns", {}).get("upper_wick", {}).get("detected")
    support = analysis.get("support", main.get("support"))
    price = analysis.get("current_price", main.get("price"))
    support_broken = bool(price is not None and support is not None and price < support)

    if position:
        gain_pct = position.get("gain_loss_pct") or position.get("profit_loss_pct") or 0
        max_loss = abs(float(position.get("max_loss_accepted_pct") or 0))
        if max_loss and gain_pct <= -max_loss and (support_broken or trend in {"BEARISH", "VERY_BEARISH"}):
            decision = "CUT_LOSS"
        elif gain_pct > 2 and (
            (resistance_distance is not None and 0 <= resistance_distance <= 1.5)
            or upper_wick
            or position.get("distance_to_tp1_pct") is not None
            and abs(float(position["distance_to_tp1_pct"])) <= 1.5
        ):
            decision = "TAKE_PROFIT"
        elif trend in {"BEARISH", "VERY_BEARISH"} or upper_wick or (
            resistance_distance is not None and 0 <= resistance_distance <= 1.5
        ):
            decision = "SELL_WATCH"
        elif context_score < 35 or risk_score < 35:
            decision = "AVOID"
        else:
            decision = "WAIT"
    elif context_score < 35 or trend == "VERY_BEARISH" or risk_score < 35:
        decision = "AVOID"
    elif (
        context_score >= 55
        and setup_score >= 60
        and trigger_score >= 60
        and risk_score >= 50
        and market_score >= 45
        and not any(item != "Trigger absent" for item in major_blocking)
    ):
        decision = "BUY_READY"
    elif (
        setup_score >= 55
        and risk_score >= 50
        and 30 <= trigger_score <= 59
        and not any(item != "Trigger absent" for item in major_blocking)
    ):
        decision = "BUY_WATCH"
    elif (
        resistance_distance is not None
        and 0 <= resistance_distance <= 1
        and upper_wick
        and main.get("momentum", {}).get("volume_acceleration", {}).get("detected")
    ):
        decision = "AVOID"
    else:
        decision = "WAIT"

    confidence, confidence_label = _confidence(
        context_score,
        setup_score,
        trigger_score,
        risk_score,
        market_score,
        analysis,
    )

    trigger_label = "Confirme" if trigger_score >= 60 else "Partiel" if trigger_score >= 30 else "Absent"
    return {
        "symbol": symbol,
        "decision": decision,
        "decision_label": DECISION_LABELS[decision],
        "confidence": confidence,
        "confidence_label": confidence_label,
        "context_score": context_score,
        "setup_score": setup_score,
        "trigger_score": trigger_score,
        "trigger_label": trigger_label,
        "risk_score": risk_score,
        "market_score": market_score,
        "market_context": market_context,
        "reason_summary": _summary(decision, blocking, positives),
        "blocking_factors": blocking,
        "positive_factors": positives,
        "is_position": bool(position),
    }


def build_decisions(
    scan_results: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    decisions = []
    for item in scan_results:
        if "error" in item:
            continue
        decisions.append(
            build_decision(
                symbol=item["symbol"],
                analysis=item,
                scan_results=scan_results,
                positions=positions,
            )
        )
    return decisions


def build_decision_v5(
    symbol: str,
    analysis: dict[str, Any],
    scan_results: list[dict[str, Any]] | None = None,
    positions: list[dict[str, Any]] | None = None,
    *,
    btc_analysis: dict[str, Any] | None = None,
    score_acceleration: dict[str, Any] | None = None,
    advanced_triggers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = build_decision(
        symbol=symbol,
        analysis=analysis,
        scan_results=scan_results,
        positions=positions,
    )
    regime = determine_market_regime(scan_results, decision.get("market_context"))
    score_acceleration = score_acceleration or calculate_score_acceleration(symbol)
    advanced_triggers = advanced_triggers or analysis.get("advanced_triggers")

    relative_strength = None
    btc_adjustment = {"adjustment": 0, "blocking": False, "reason": "BTC context non disponible."}
    if btc_analysis and symbol.upper() != "BTCUSDC":
        relative_strength = calculate_relative_strength(analysis, btc_analysis)
        btc_adjustment = btc_altcoin_adjustment(symbol, decision.get("market_context", {}), relative_strength)
        decision["market_score"] = _clamp(decision["market_score"] + btc_adjustment["adjustment"])

    if advanced_triggers and advanced_triggers.get("detected"):
        decision["trigger_score"] = _clamp(decision["trigger_score"] + min(18, advanced_triggers.get("score", 0) * 0.25))
        if "Trigger avance" not in decision["positive_factors"]:
            decision["positive_factors"].append("Trigger avance")

    if (
        decision["decision"] == "WAIT"
        and score_acceleration.get("acceleration") == "STRONG"
        and decision["setup_score"] >= 48
        and decision["risk_score"] >= 45
        and regime["regime"] in {"RISK_ON", "SPECULATIVE", "NEUTRAL"}
    ):
        decision["decision"] = "BUY_WATCH"
        decision["decision_label"] = DECISION_LABELS["BUY_WATCH"]
        decision["reason_summary"] = "Score en acceleration forte, setup a surveiller malgre un trigger encore incomplet."
        decision["positive_factors"].append("Score acceleration forte")

    if btc_adjustment.get("blocking") and decision["decision"] == "BUY_READY":
        decision["decision"] = "BUY_WATCH"
        decision["decision_label"] = DECISION_LABELS["BUY_WATCH"]
        decision["blocking_factors"].append("BTC VERY_BEARISH")

    decision["v5"] = {
        "market_regime": regime,
        "score_acceleration": score_acceleration,
        "relative_strength": relative_strength,
        "btc_adjustment": btc_adjustment,
        "advanced_triggers": advanced_triggers,
    }
    return decision
