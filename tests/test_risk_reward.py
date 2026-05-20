from services.risk_reward import calculate_risk_reward


def test_calculate_risk_reward_good_quality() -> None:
    result = calculate_risk_reward(price=100.0, support=95.0, resistance=112.0)

    assert result["risk_pct"] == 5.0
    assert result["reward_pct"] == 12.0
    assert result["ratio"] == 2.4
    assert result["quality"] == "BON"


def test_calculate_risk_reward_invalid_levels() -> None:
    result = calculate_risk_reward(price=100.0, support=101.0, resistance=120.0)

    assert result["ratio"] == 0.0
    assert result["quality"] == "INVALID"
