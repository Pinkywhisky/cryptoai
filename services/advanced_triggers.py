from __future__ import annotations

from typing import Any

import pandas as pd


def _result(detected: bool, score: int, label: str, reason: str) -> dict[str, Any]:
    return {
        "detected": bool(detected),
        "score": score if detected else 0,
        "label": label,
        "reason": reason,
    }


def _last(df: pd.DataFrame, column: str, offset: int = 1) -> float | None:
    if df.empty or column not in df.columns or len(df) < offset:
        return None
    value = pd.to_numeric(pd.Series([df.iloc[-offset][column]]), errors="coerce").iloc[0]
    return None if pd.isna(value) else float(value)


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def detect_volatility_squeeze(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 25:
        return _result(False, 14, "Volatility squeeze", "Historique insuffisant.")
    ranges = (pd.to_numeric(df["high"]) - pd.to_numeric(df["low"])) / pd.to_numeric(df["close"])
    detected = bool(ranges.tail(8).mean() < ranges.tail(25).mean() * 0.65)
    return _result(detected, 14, "Volatility squeeze", "Compression de volatilite avant expansion possible.")


def detect_fake_breakout(df: pd.DataFrame, resistance: float | None = None) -> dict[str, Any]:
    if len(df) < 3:
        return _result(False, 20, "Fake breakout", "Historique insuffisant.")
    resistance = resistance or float(pd.to_numeric(df["high"].iloc[-12:-2]).max())
    previous_close = _last(df, "close", 2)
    close = _last(df, "close")
    high = _last(df, "high")
    detected = bool(previous_close and close and high and previous_close > resistance and high > resistance and close < resistance)
    return _result(detected, 20, "Fake breakout", "Cassure invalidee avec cloture sous resistance.")


def detect_resistance_rejection(df: pd.DataFrame, resistance: float | None = None) -> dict[str, Any]:
    if len(df) < 12:
        return _result(False, 16, "Resistance rejection", "Historique insuffisant.")
    resistance = resistance or float(pd.to_numeric(df["high"].iloc[-12:-1]).max())
    last = df.iloc[-1]
    high = float(last["high"])
    close = float(last["close"])
    open_price = float(last["open"])
    upper_wick = high - max(open_price, close)
    detected = bool(high >= resistance * 0.995 and close < resistance and upper_wick > _body(last))
    return _result(detected, 16, "Resistance rejection", "Rejet net sous resistance avec meche haute.")


def detect_support_rejection(df: pd.DataFrame, support: float | None = None) -> dict[str, Any]:
    if len(df) < 12:
        return _result(False, 16, "Support rejection", "Historique insuffisant.")
    support = support or float(pd.to_numeric(df["low"].iloc[-12:-1]).min())
    last = df.iloc[-1]
    low = float(last["low"])
    close = float(last["close"])
    open_price = float(last["open"])
    lower_wick = min(open_price, close) - low
    detected = bool(low <= support * 1.005 and close > support and lower_wick > _body(last))
    return _result(detected, 16, "Support rejection", "Defense du support avec meche basse.")


def detect_bullish_divergence(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 8 or "rsi" not in df.columns:
        return _result(False, 18, "Bullish divergence", "RSI indisponible.")
    recent = df.tail(8)
    price_lower_low = float(recent["low"].iloc[-1]) < float(recent["low"].iloc[:4].min())
    rsi_higher_low = float(recent["rsi"].iloc[-1]) > float(recent["rsi"].iloc[:4].min())
    return _result(price_lower_low and rsi_higher_low, 18, "Bullish divergence", "RSI remonte pendant que le prix teste un plus bas.")


def detect_bearish_divergence(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 8 or "rsi" not in df.columns:
        return _result(False, 18, "Bearish divergence", "RSI indisponible.")
    recent = df.tail(8)
    price_higher_high = float(recent["high"].iloc[-1]) > float(recent["high"].iloc[:4].max())
    rsi_lower_high = float(recent["rsi"].iloc[-1]) < float(recent["rsi"].iloc[:4].max())
    return _result(price_higher_high and rsi_lower_high, 18, "Bearish divergence", "RSI faiblit pendant que le prix tente un plus haut.")


def detect_absorption_volume(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 21:
        return _result(False, 15, "Absorption volume", "Historique insuffisant.")
    last = df.iloc[-1]
    volume = float(last["volume"])
    avg_volume = float(pd.to_numeric(df["volume"].tail(20)).mean())
    spread = (float(last["high"]) - float(last["low"])) / max(float(last["close"]), 1e-9)
    avg_spread = float(((pd.to_numeric(df["high"].tail(20)) - pd.to_numeric(df["low"].tail(20))) / pd.to_numeric(df["close"].tail(20))).mean())
    detected = bool(volume > avg_volume * 1.8 and spread < avg_spread * 0.75)
    return _result(detected, 15, "Absorption volume", "Volume important avec faible progression du prix.")


def detect_momentum_acceleration(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 8:
        return _result(False, 16, "Momentum acceleration", "Historique insuffisant.")
    closes = pd.to_numeric(df["close"].tail(8))
    recent = closes.pct_change().tail(3).mean()
    previous = closes.pct_change().head(4).mean()
    detected = bool(recent > 0 and recent > previous * 1.8)
    return _result(detected, 16, "Momentum acceleration", "Progression recente plus forte que le rythme precedent.")


def detect_exhaustion_candle(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 21:
        return _result(False, 16, "Exhaustion candle", "Historique insuffisant.")
    last = df.iloc[-1]
    avg_body = float((pd.to_numeric(df["close"].tail(20)) - pd.to_numeric(df["open"].tail(20))).abs().mean())
    body = _body(last)
    volume = float(last["volume"])
    avg_volume = float(pd.to_numeric(df["volume"].tail(20)).mean())
    upper_wick = float(last["high"]) - max(float(last["open"]), float(last["close"]))
    detected = bool(body > avg_body * 1.6 and volume > avg_volume * 1.5 and upper_wick > body * 0.7)
    return _result(detected, 16, "Exhaustion candle", "Grande bougie avec volume et meche de fatigue.")


def detect_strong_reclaim(df: pd.DataFrame, level: float | None = None) -> dict[str, Any]:
    if len(df) < 2:
        return _result(False, 18, "Strong reclaim", "Historique insuffisant.")
    level = level or _last(df, "ma25")
    previous_close = _last(df, "close", 2)
    close = _last(df, "close")
    open_price = _last(df, "open")
    detected = bool(level and previous_close and close and open_price and previous_close < level < close and close > open_price)
    return _result(detected, 18, "Strong reclaim", "Reprise nette d'un niveau cle en cloture.")


def detect_advanced_triggers(
    df: pd.DataFrame,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = analysis or {}
    resistance = analysis.get("resistance")
    support = analysis.get("support")
    checks = [
        detect_volatility_squeeze(df),
        detect_fake_breakout(df, resistance),
        detect_resistance_rejection(df, resistance),
        detect_support_rejection(df, support),
        detect_bullish_divergence(df),
        detect_bearish_divergence(df),
        detect_absorption_volume(df),
        detect_momentum_acceleration(df),
        detect_exhaustion_candle(df),
        detect_strong_reclaim(df),
    ]
    score = min(100, sum(item["score"] for item in checks if item["detected"]))
    detected = [item for item in checks if item["detected"]]
    return {
        "detected": bool(detected),
        "score": int(score),
        "label": detected[0]["label"] if detected else "Aucun trigger avance",
        "reason": detected[0]["reason"] if detected else "Aucun trigger avance confirme.",
        "signals": checks,
    }
