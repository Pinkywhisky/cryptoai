import pandas as pd

from services.scoring import analyze_market


def test_analyze_market_returns_bullish_score() -> None:
    df = pd.DataFrame(
        [
            {
                "close": 105.0,
                "volume": 200.0,
                "ma7": 100.0,
                "ma25": 90.0,
                "ma99": 80.0,
                "volume_avg_20": 100.0,
                "rsi": 55.0,
            }
        ]
    )

    result = analyze_market(df)

    assert result["score"] >= 70
    assert result["signal"] == "ACHAT POTENTIEL"
    assert result["price"] == 105.0
    assert result["reasons"]


def test_analyze_market_returns_bearish_score() -> None:
    df = pd.DataFrame(
        [
            {
                "close": 80.0,
                "volume": 50.0,
                "ma7": 90.0,
                "ma25": 100.0,
                "ma99": 110.0,
                "volume_avg_20": 100.0,
                "rsi": 80.0,
            }
        ]
    )

    result = analyze_market(df)

    assert result["score"] < 45
    assert result["signal"] == "ÉVITER / VENTE POSSIBLE"
    assert result["reasons"]


def test_analyze_market_returns_setup_quality() -> None:
    df = pd.DataFrame(
        {
            "open": [100.0 for _ in range(120)],
            "high": [125.0 for _ in range(120)],
            "low": [95.0 for _ in range(120)],
            "close": [100.0 for _ in range(120)],
            "volume": [100.0 for _ in range(120)],
            "taker_buy_base_volume": [50.0 for _ in range(120)],
            "ma7": [100.0 for _ in range(120)],
            "ma25": [100.0 for _ in range(120)],
            "ma99": [100.0 for _ in range(120)],
            "volume_avg_20": [100.0 for _ in range(120)],
            "rsi": [50.0 for _ in range(120)],
        }
    )

    result = analyze_market(df)

    assert result["setup_quality"] == "EXCELLENT"
    assert result["risk_reward"]["quality"] == "EXCELLENT"
    assert result["support"] == 95.0
    assert result["resistance"] == 125.0


def test_setup_quality_is_limited_by_bearish_trend() -> None:
    df = pd.DataFrame(
        {
            "open": [120.0 - index * 0.15 for index in range(120)],
            "high": [130.0 for _ in range(120)],
            "low": [99.0 for _ in range(120)],
            "close": [120.0 - index * 0.15 for index in range(120)],
            "volume": [80.0 for _ in range(120)],
            "taker_buy_base_volume": [30.0 for _ in range(120)],
            "ma7": [112.0 - index * 0.08 for index in range(120)],
            "ma25": [118.0 - index * 0.05 for index in range(120)],
            "ma99": [126.0 - index * 0.02 for index in range(120)],
            "volume_avg_20": [100.0 for _ in range(120)],
            "rsi": [38.0 for _ in range(120)],
        }
    )

    result = analyze_market(df)

    assert result["risk_reward"]["quality"] == "EXCELLENT"
    assert result["trend_strength"]["trend"] in {"BEARISH", "VERY_BEARISH"}
    assert result["setup_quality"] in {"MAUVAIS", "MOYEN"}
    assert result["setup_quality"] != "EXCELLENT"


def test_setup_quality_excellent_impossible_under_ma25_and_ma99() -> None:
    df = pd.DataFrame(
        {
            "open": [100.0 for _ in range(120)],
            "high": [130.0 for _ in range(120)],
            "low": [99.0 for _ in range(120)],
            "close": [100.0 for _ in range(120)],
            "volume": [120.0 for _ in range(120)],
            "taker_buy_base_volume": [60.0 for _ in range(120)],
            "ma7": [101.0 for _ in range(120)],
            "ma25": [110.0 for _ in range(120)],
            "ma99": [120.0 for _ in range(120)],
            "volume_avg_20": [100.0 for _ in range(120)],
            "rsi": [45.0 for _ in range(120)],
        }
    )

    result = analyze_market(df)

    assert result["risk_reward"]["quality"] == "EXCELLENT"
    assert result["price"] < result["ma25"]
    assert result["price"] < result["ma99"]
    assert result["setup_quality"] != "EXCELLENT"
