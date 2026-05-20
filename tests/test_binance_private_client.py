import os

from services.binance_private_client import BinancePrivateClient
from services.config import get_binance_private_config


def test_binance_config_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")

    config = get_binance_private_config()

    assert config.configured is True
    assert config.use_testnet is True


def test_private_client_signature_is_deterministic() -> None:
    client = BinancePrivateClient(
        config=type(
            "Config",
            (),
            {"api_key": "key", "api_secret": "secret", "use_testnet": False, "configured": True},
        )()
    )

    signature = client._sign({"symbol": "BTCUSDC", "timestamp": 1})

    assert len(signature) == 64


def test_private_client_returns_empty_when_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)

    config = get_binance_private_config()

    assert config.configured is False


def test_get_non_zero_balances_with_mocked_request(monkeypatch) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "balances": [
                    {"asset": "BTC", "free": "0.1", "locked": "0"},
                    {"asset": "ETH", "free": "0", "locked": "0"},
                ]
            }

    client = BinancePrivateClient(
        config=type(
            "Config",
            (),
            {"api_key": "key", "api_secret": "secret", "use_testnet": False, "configured": True},
        )()
    )
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())

    balances = client.get_non_zero_balances()

    assert balances == [{"asset": "BTC", "free": 0.1, "locked": 0.0, "total": 0.1}]
