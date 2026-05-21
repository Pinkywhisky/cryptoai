from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from services.market_regime import determine_market_regime


TYPE_RANK = {"RISK": 0, "OPPORTUNITY": 1, "WATCH": 2, "MARKET": 3}
SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _alert(
    *,
    alert_type: str,
    severity: str,
    symbol: str | None,
    title: str,
    message: str,
    confidence: int,
    source: str,
) -> dict[str, Any]:
    created_at = _now()
    return {
        "id": f"{alert_type}:{symbol or 'MARKET'}:{title}".upper().replace(" ", "_"),
        "type": alert_type,
        "severity": severity,
        "symbol": symbol,
        "title": title,
        "message": message,
        "confidence": max(0, min(100, int(confidence or 0))),
        "created_at": created_at,
        "source": source,
    }


def build_opportunity_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        decision = row.get("decision_engine") or {}
        code = decision.get("decision")
        if code not in {"BUY_READY", "BUY_WATCH"}:
            continue
        confidence = int(decision.get("confidence") or 0)
        if confidence < 40:
            continue
        severity = "HIGH" if code == "BUY_READY" or confidence >= 70 else "MEDIUM"
        alerts.append(
            _alert(
                alert_type="OPPORTUNITY",
                severity=severity,
                symbol=row.get("symbol"),
                title=decision.get("decision_label") or "Opportunité à surveiller",
                message=decision.get("reason_summary") or "Setup constructif détecté.",
                confidence=confidence,
                source="DECISION_ENGINE",
            )
        )
    return alerts


def build_risk_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        decision = row.get("decision_engine") or {}
        code = decision.get("decision")
        if code not in {"CUT_LOSS", "SELL_WATCH", "TAKE_PROFIT"}:
            continue
        if code == "TAKE_PROFIT":
            alert_type = "OPPORTUNITY"
            title = "Prise de bénéfice possible"
        else:
            alert_type = "RISK"
            title = decision.get("decision_label") or "Risque position"
        severity = "HIGH" if code == "CUT_LOSS" else "MEDIUM"
        alerts.append(
            _alert(
                alert_type=alert_type,
                severity=severity,
                symbol=row.get("symbol"),
                title=title,
                message=(decision.get("blocking_factors") or [decision.get("reason_summary") or "Position à surveiller."])[0],
                confidence=decision.get("confidence") or 50,
                source="DECISION_ENGINE",
            )
        )
    return alerts


def build_watch_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        decision = row.get("decision_engine") or {}
        if not row.get("watch_info") and "WATCHLIST" not in row.get("sources", []):
            continue
        if decision.get("decision") != "WAIT":
            continue
        trigger_score = int(decision.get("trigger_score") or 0)
        setup_score = int(decision.get("setup_score") or 0)
        if setup_score < 45 and trigger_score < 25:
            continue
        alerts.append(
            _alert(
                alert_type="WATCH",
                severity="MEDIUM",
                symbol=row.get("symbol"),
                title="Setup en construction",
                message=decision.get("reason_summary") or "Surveillance utile, mais trigger encore incomplet.",
                confidence=decision.get("confidence") or 45,
                source="WATCHLIST",
            )
        )
    return alerts


def build_alpha_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        position = row.get("position_info") or {}
        watch = row.get("watch_info") or {}
        if (position.get("market_source") or watch.get("market_source")) != "BINANCE_ALPHA":
            continue
        if not row.get("position_info") and not row.get("watch_info"):
            continue
        pnl_pct = position.get("unrealized_pnl_pct")
        if pnl_pct is not None and float(pnl_pct) <= -20:
            alerts.append(
                _alert(
                    alert_type="RISK",
                    severity="HIGH",
                    symbol=row.get("symbol"),
                    title="Perte latente Alpha élevée",
                    message=f"Perte latente Alpha supérieure à 20% ({round(float(pnl_pct), 2)}%).",
                    confidence=70,
                    source="BINANCE_ALPHA",
                )
            )
            continue
        change_pct = row.get("price_change_pct") or position.get("price_change_pct") or watch.get("price_change_pct")
        quote_volume = row.get("quote_volume") or position.get("quote_volume") or watch.get("quote_volume")
        if change_pct is not None and float(change_pct) >= 8:
            alerts.append(
                _alert(
                    alert_type="OPPORTUNITY",
                    severity="MEDIUM",
                    symbol=row.get("symbol"),
                    title="Rebond Alpha détecté",
                    message=f"Variation Alpha 24h positive de {round(float(change_pct), 2)}%.",
                    confidence=60,
                    source="BINANCE_ALPHA",
                )
            )
            continue
        if quote_volume is not None and float(quote_volume) < 50_000:
            title = "Volume Alpha faible"
            message = "Volume Alpha en baisse ou insuffisant pour une entrée agressive."
        else:
            title = "Token Alpha"
            message = f"{row.get('symbol')} est un token Alpha : analyse technique Spot indisponible."
        alerts.append(
            _alert(
                alert_type="WATCH",
                severity="MEDIUM",
                symbol=row.get("symbol"),
                title=title,
                message=message,
                confidence=55,
                source="BINANCE_ALPHA",
            )
        )
    return alerts


def _score_history(row: dict[str, Any]) -> list[float]:
    raw_history = row.get("score_history") or row.get("history") or []
    values: list[float] = []
    for item in raw_history:
        try:
            values.append(float(item.get("global_score") if isinstance(item, dict) else item))
        except (TypeError, ValueError):
            continue
    return values


def build_change_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        symbol = row.get("symbol")
        history = _score_history(row)
        if len(history) >= 2:
            delta = history[-1] - history[-2]
            if delta >= 12:
                alerts.append(
                    _alert(
                        alert_type="OPPORTUNITY",
                        severity="MEDIUM",
                        symbol=symbol,
                        title="Momentum en forte amélioration",
                        message=f"Le score progresse de {round(delta, 1)} points depuis le dernier snapshot.",
                        confidence=min(80, 55 + int(delta)),
                        source="SCORE_HISTORY",
                    )
                )
                continue
            if delta <= -12:
                alerts.append(
                    _alert(
                        alert_type="RISK",
                        severity="MEDIUM",
                        symbol=symbol,
                        title="Score en dégradation rapide",
                        message=f"Le score recule de {abs(round(delta, 1))} points depuis le dernier snapshot.",
                        confidence=min(80, 55 + int(abs(delta))),
                        source="SCORE_HISTORY",
                    )
                )
                continue

        change_pct = row.get("price_change_pct")
        quote_volume = row.get("quote_volume")
        try:
            change = float(change_pct)
            volume = float(quote_volume or 0)
        except (TypeError, ValueError):
            continue
        if change >= 4 and volume >= 100_000:
            alerts.append(
                _alert(
                    alert_type="OPPORTUNITY",
                    severity="MEDIUM",
                    symbol=symbol,
                    title="Momentum prix-volume positif",
                    message=f"Variation positive de {round(change, 2)}% avec volume exploitable.",
                    confidence=62,
                    source="MARKET_DATA",
                )
            )
        elif change <= -6:
            alerts.append(
                _alert(
                    alert_type="RISK",
                    severity="MEDIUM",
                    symbol=symbol,
                    title="Pression vendeuse récente",
                    message=f"Variation négative de {abs(round(change, 2))}% à surveiller.",
                    confidence=60,
                    source="MARKET_DATA",
                )
            )
    return alerts


def build_market_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    regime = determine_market_regime([row for row in rows if "error" not in row])
    if regime.get("confidence", 0) < 50 and regime.get("regime") == "NEUTRAL":
        return []
    severity = "HIGH" if regime.get("regime") in {"PANIC", "RISK_OFF"} else "MEDIUM"
    return [
        _alert(
            alert_type="MARKET",
            severity=severity,
            symbol=None,
            title=f"Marché {regime.get('regime', 'NEUTRAL')}",
            message=regime.get("message") or "Contexte marché à surveiller.",
            confidence=regime.get("confidence") or 50,
            source="MARKET_REGIME",
        )
    ]


def generate_ai_alerts(rows: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    alerts = (
        build_risk_alerts(rows)
        + build_opportunity_alerts(rows)
        + build_watch_alerts(rows)
        + build_alpha_alerts(rows)
        + build_change_alerts(rows)
        + build_market_alerts(rows)
    )
    deduped: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        key = f"{alert['type']}:{alert.get('symbol')}:{alert['title']}"
        current = deduped.get(key)
        if not current or SEVERITY_RANK[alert["severity"]] < SEVERITY_RANK[current["severity"]]:
            deduped[key] = alert
    result = list(deduped.values())
    result.sort(key=lambda item: (TYPE_RANK[item["type"]], SEVERITY_RANK[item["severity"]], -item["confidence"]))
    if not result and rows:
        result.append(
            _alert(
                alert_type="MARKET",
                severity="LOW",
                symbol=None,
                title="Marché sous observation",
                message="Aucune alerte critique, mais le cockpit continue de suivre les variations de prix, score et tendance.",
                confidence=45,
                source="AI_ALERTS",
            )
        )
    return result[:limit]
