import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import requests

from services.config import BinancePrivateConfig, get_binance_private_config


SPOT_BASE_URL = "https://api.binance.com"
SPOT_TESTNET_BASE_URL = "https://testnet.binance.vision"


class BinancePrivateClient:
    def __init__(self, config: BinancePrivateConfig | None = None) -> None:
        self.config = config or get_binance_private_config()
        self.base_url = SPOT_TESTNET_BASE_URL if self.config.use_testnet else SPOT_BASE_URL

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, params: dict[str, Any]) -> str:
        if not self.config.api_secret:
            raise RuntimeError("Binance private API secret is not configured")
        query_string = urlencode(params, doseq=True)
        return hmac.new(
            self.config.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise RuntimeError("Binance private API is not configured")

        signed_params = {
            **(params or {}),
            "timestamp": self._timestamp(),
            "recvWindow": 5000,
        }
        signed_params["signature"] = self._sign(signed_params)

        try:
            response = requests.get(
                f"{self.base_url}{path}",
                params=signed_params,
                headers={"X-MBX-APIKEY": self.config.api_key or ""},
                timeout=10,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Binance private HTTP error: {response.text}") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Binance private request failed: {exc}") from exc

        data = response.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0):
            raise RuntimeError(f"Binance private API error: {data}")
        return data

    def get_account_balances(self) -> list[dict[str, Any]]:
        account = self._signed_get("/api/v3/account")
        return account.get("balances", [])

    def get_non_zero_balances(self) -> list[dict[str, Any]]:
        balances = self.get_account_balances()
        return [
            {
                "asset": item["asset"],
                "free": float(item.get("free", 0)),
                "locked": float(item.get("locked", 0)),
                "total": float(item.get("free", 0)) + float(item.get("locked", 0)),
            }
            for item in balances
            if float(item.get("free", 0)) + float(item.get("locked", 0)) > 0
        ]

    def get_my_trades(self, symbol: str) -> list[dict[str, Any]]:
        return self._signed_get("/api/v3/myTrades", {"symbol": symbol.upper()})

    def get_order_history(self, symbol: str) -> list[dict[str, Any]]:
        return self._signed_get("/api/v3/allOrders", {"symbol": symbol.upper()})

    def get_account_snapshot(self) -> dict[str, Any]:
        return self._signed_get("/sapi/v1/accountSnapshot", {"type": "SPOT"})

    def get_current_holdings(self) -> list[dict[str, Any]]:
        return self.get_non_zero_balances()


def get_private_client_status() -> dict[str, Any]:
    config = get_binance_private_config()
    return {
        "configured": config.configured,
        "read_only_mode": True,
        "use_testnet": config.use_testnet,
    }


def get_account_balances() -> list[dict[str, Any]]:
    return BinancePrivateClient().get_account_balances()


def get_non_zero_balances() -> list[dict[str, Any]]:
    return BinancePrivateClient().get_non_zero_balances()


def get_my_trades(symbol: str) -> list[dict[str, Any]]:
    return BinancePrivateClient().get_my_trades(symbol)


def get_order_history(symbol: str) -> list[dict[str, Any]]:
    return BinancePrivateClient().get_order_history(symbol)


def get_account_snapshot() -> dict[str, Any]:
    return BinancePrivateClient().get_account_snapshot()


def get_current_holdings() -> list[dict[str, Any]]:
    return BinancePrivateClient().get_current_holdings()
