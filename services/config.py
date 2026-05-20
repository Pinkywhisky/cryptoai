import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class BinancePrivateConfig:
    api_key: str | None
    api_secret: str | None
    use_testnet: bool

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


def get_binance_private_config() -> BinancePrivateConfig:
    use_testnet = os.getenv("BINANCE_USE_TESTNET", "false").lower() == "true"
    return BinancePrivateConfig(
        api_key=os.getenv("BINANCE_API_KEY") or None,
        api_secret=os.getenv("BINANCE_API_SECRET") or None,
        use_testnet=use_testnet,
    )


def get_min_active_position_value_usdc() -> float:
    raw_value = os.getenv("MIN_ACTIVE_POSITION_VALUE_USDC", "1.00")
    try:
        value = float(raw_value)
    except ValueError:
        return 1.0
    return max(value, 0.0)
