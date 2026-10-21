"""Validation at the HTTP boundary; storage and financial calculations are unchanged."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonNegative = Annotated[Number, Field(ge=0)]
Positive = Annotated[Number, Field(gt=0)]
Percent = Annotated[Number, Field(ge=0, le=100)]
Score = Annotated[int, Field(strict=True, ge=0, le=100)]
Text = Annotated[str, Field(max_length=4000)]
MarketSource = Literal["BINANCE_SPOT", "BINANCE_ALPHA", "MANUAL", "UNKNOWN", "BINANCE", "MIXED"]
Symbol = Annotated[str, Field(min_length=1, max_length=40)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_max_length=4000)

    @field_validator("symbol", "resolved_symbol", "base_asset", check_fields=False)
    @classmethod
    def valid_symbol(cls, value):
        if value is None:
            return value
        value = value.strip().upper()
        # Preserve established base / pair spellings accepted by the resolver.
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{0,39}", value):
            raise ValueError("Invalid symbol")
        return value

    @field_validator("source", "status", "market_source", "priority", "decision_type", "action", "trade_type", "emotion", "result_status", "result", check_fields=False, mode="before")
    @classmethod
    def normalize_choice(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    def payload(self):
        return self.model_dump(exclude_unset=True)


class PositionUpdate(Payload):
    symbol: Symbol | None = None
    quantity: Positive | None = None
    average_buy_price: NonNegative | None = None
    invested_amount: NonNegative | None = None
    strategy_type: Annotated[str, Field(max_length=64)] | None = None
    take_profit_1: Positive | None = None
    take_profit_2: Positive | None = None
    stop_loss: Positive | None = None
    max_loss_accepted_pct: Percent | None = None
    note: Text | None = None
    source: Literal["MANUAL", "BINANCE", "MIXED"] | None = None
    status: Literal["MANUAL", "ACTIVE", "INACTIVE", "CLOSED", "DUST", "INVALID"] | None = None
    market_source: MarketSource | None = None
    resolved_symbol: Symbol | None = None
    base_asset: Symbol | None = None

    @model_validator(mode="after")
    def no_null_required_columns(self):
        for name in {"symbol", "quantity", "average_buy_price", "invested_amount", "source", "status"} & self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError("A required field cannot be null")
        return self


class PositionCreate(PositionUpdate):
    symbol: Symbol
    quantity: Positive
    average_buy_price: NonNegative
    invested_amount: NonNegative


class WatchUpdate(Payload):
    symbol: Symbol | None = None
    target_buy_price: Positive | None = None
    invalidation_price: Positive | None = None
    priority: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    reason: Text | None = None
    note: Text | None = None
    market_source: MarketSource | None = None
    resolved_symbol: Symbol | None = None
    base_asset: Symbol | None = None

    @model_validator(mode="after")
    def no_null_identity(self):
        if ("symbol" in self.model_fields_set and self.symbol is None) or ("priority" in self.model_fields_set and self.priority is None):
            raise ValueError("Symbol and priority cannot be null")
        return self


class WatchCreate(WatchUpdate):
    symbol: Symbol


Decision = Literal["BUY", "SELL", "HOLD", "WAIT", "REINFORCE", "CUT_LOSS", "TAKE_PROFIT", "BUY_READY", "BUY_WATCH", "SELL_WATCH", "AVOID", "ATTENTE", "ACHAT", "VENTE"]
Result = Literal["OPEN", "WIN", "LOSS", "BREAKEVEN", "CANCELLED"]


class JournalUpdate(Payload):
    symbol: Symbol | None = None
    market_source: MarketSource | None = None
    decision_type: Decision | None = None
    action: Decision | None = None
    trade_type: Literal["TEST", "SCALP", "SWING", "LONG_TERM", "FOMO", "REVENGE"] | None = None
    emotion: Literal["CALM", "CONFIDENT", "FOMO", "FEAR", "IMPATIENT", "STRESSED", "GREED", "REVENGE"] | None = None
    confidence: Score | None = None
    reason: Text | None = None
    notes: Text | None = None
    result_status: Result | None = None
    result: Result | None = None
    pnl_percent: Number | None = None
    pnl_usdc: Number | None = None
    duration_minutes: Annotated[int, Field(strict=True, ge=0, le=52560000)] | None = None
    # Retain the legacy API fields, although the service computes its own snapshot.
    ai_score: Score | None = None
    ai_trend: Annotated[str, Field(max_length=64)] | None = None
    ai_setup_quality: Annotated[str, Field(max_length=64)] | None = None
    ai_trigger: Annotated[str, Field(max_length=128)] | None = None
    ai_market_regime: Annotated[str, Field(max_length=64)] | None = None
    ai_market_score: Score | None = None
    ai_snapshot_json: Annotated[str, Field(max_length=32768)] | None = None

    @model_validator(mode="after")
    def valid_identity_and_aliases(self):
        if "symbol" in self.model_fields_set and self.symbol is None:
            raise ValueError("Symbol cannot be null")
        if self.action and self.decision_type and self.action != self.decision_type:
            raise ValueError("Conflicting decision aliases")
        if self.result and self.result_status and self.result != self.result_status:
            raise ValueError("Conflicting result aliases")
        return self


class JournalCreate(JournalUpdate):
    symbol: Symbol


class Credentials(Payload):
    password: Annotated[str, Field(max_length=1024)] = Field(default="", repr=False)
    csrf_token: Annotated[str, Field(max_length=128)] | None = Field(default=None, repr=False)
    confirmation: Literal["RESET", "CLEANUP", ""] = ""
