from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SH = ZoneInfo("Asia/Shanghai")


def decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite decimal")
    return result


def money(value: Any) -> Decimal:
    from decimal import ROUND_HALF_UP

    return decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def stable_id(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:24]


def at(day: date, hour: int = 21, minute: int = 30) -> datetime:
    return datetime.combine(day, datetime.min.time(), SH).replace(hour=hour, minute=minute)


class Mode(StrEnum):
    DEMO = "demo"
    RESEARCH = "research"
    PAPER = "paper"
    MANUAL = "manual"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"

    @field_validator("*", mode="after")
    @classmethod
    def aware(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include timezone")
        return value.astimezone(SH) if isinstance(value, datetime) else value


class Instrument(Contract):
    instrument_id: str
    symbol: str
    exchange: Literal["XSHG", "XSHE"]
    asset_type: Literal["stock", "etf"]
    name: str
    board: Literal["MAIN", "EQUITY_ETF"]
    listed_on: date
    delisted_on: date | None = None
    tick: Decimal = Field(gt=0)
    lot: int = Field(default=100, gt=0)
    currency: Literal["CNY"] = "CNY"
    settlement: Literal["T+1"] = "T+1"

    @model_validator(mode="after")
    def mapping(self):
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", self.symbol):
            raise ValueError("symbol requires six digits and SH/SZ suffix")
        if (self.symbol.endswith("SH")) != (self.exchange == "XSHG"):
            raise ValueError("symbol/exchange mismatch")
        if self.instrument_id != self.symbol:
            raise ValueError("v1 canonical instrument_id must equal symbol")
        if self.asset_type == "stock" and not self.symbol.startswith(
            ("600", "601", "603", "605", "000", "001", "002", "003")
        ):
            raise ValueError("unsupported stock board")
        if self.board != ("MAIN" if self.asset_type == "stock" else "EQUITY_ETF"):
            raise ValueError("asset/board mismatch")
        if self.tick != (Decimal("0.01") if self.asset_type == "stock" else Decimal("0.001")):
            raise ValueError("wrong tick")
        return self


class Bar(Contract):
    instrument_id: str
    trade_date: date
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(ge=0)
    amount: Decimal = Field(ge=0)
    factor: Decimal = Field(gt=0)
    suspended: bool = False
    st: bool = False
    delisting_period: bool = False
    limit_up: Decimal = Field(gt=0)
    limit_down: Decimal = Field(gt=0)
    available_at: datetime
    observed_at: datetime
    availability_basis: Literal["historical_assumption", "observed"]

    @model_validator(mode="after")
    def range_check(self):
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("invalid OHLC range")
        if self.limit_down > self.limit_up:
            raise ValueError("invalid price limits")
        if self.availability_basis == "observed" and self.available_at < self.observed_at:
            raise ValueError("observed data cannot be available before observation")
        if self.available_at.astimezone(SH).date() < self.trade_date:
            raise ValueError("bar available before market date")
        return self


class CalendarDay(Contract):
    exchange: Literal["XSHG", "XSHE"]
    trade_date: date
    is_open: bool


class CorporateAction(Contract):
    action_id: str
    instrument_id: str
    kind: Literal["dividend", "bonus", "delisting", "unsupported"]
    record_date: date
    ex_date: date
    pay_date: date
    unlock_date: date | None = None
    cash_per_share: Decimal = Field(default=Decimal(0), ge=0)
    bonus_ratio: Decimal = Field(default=Decimal(0), ge=0)
    published_at: datetime
    observed_at: datetime
    available_at: datetime
    implemented: bool = True


class Snapshot(Contract):
    snapshot_id: str
    provider: str
    scope: Literal["synthetic_sample", "real_sample", "historical_full"]
    files: dict[str, str]
    hashes: dict[str, str]
    start: date
    end: date
    quality: dict[str, Any]
    availability: Literal["historical_assumption", "observed"]
    transform_version: str = "1.0"


class TargetPortfolio(Contract):
    target_id: str
    strategy_id: str
    strategy_version: str = "1.0"
    decision_at: datetime
    trade_not_before: date
    expires_at: date
    snapshot_id: str
    weights: dict[str, Decimal]
    reasons: dict[str, str]
    status: Literal["REBALANCE", "NO_REBALANCE"] = "REBALANCE"

    @field_validator("weights")
    @classmethod
    def positive(cls, weights):
        if any(not x.is_finite() or x < 0 or x > 1 for x in weights.values()):
            raise ValueError("invalid weights")
        if sum(weights.values()) > 1:
            raise ValueError("target exceeds account")
        return weights

    @model_validator(mode="after")
    def timing(self):
        if self.trade_not_before <= self.decision_at.astimezone(SH).date():
            raise ValueError("execution must follow decision day")
        if self.expires_at < self.trade_not_before:
            raise ValueError("invalid target lifetime")
        return self


class OrderIntent(Contract):
    intent_id: str
    plan_id: str
    account_id: str
    target_id: str
    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    reference_price: Decimal = Field(gt=0)
    valid_date: date
    estimated_fees: Decimal = Field(ge=0)
    reason: str


class ExecutionEvent(Contract):
    event_id: str
    account_id: str
    mode: Mode
    kind: Literal[
        "Deposit",
        "Withdrawal",
        "OrderRegistered",
        "Fill",
        "FeeAdjusted",
        "DividendEntitled",
        "DividendPaid",
        "DividendTaxPaid",
        "SplitOrBonus",
        "SharesUnlocked",
        "AssetWrittenDown",
        "CashRecovery",
        "Correction",
        "AccountInitialized",
    ]
    event_time: datetime
    received_at: datetime
    source: str
    source_batch: str
    payload: dict[str, Any]


class PositionView(Contract):
    quantity: int = Field(ge=0)
    sellable: int = Field(ge=0)
    frozen: int = Field(default=0, ge=0)


class AccountView(Contract):
    account_id: str
    mode: Mode
    asof: datetime
    cash_total: Decimal
    cash_available: Decimal
    cash_frozen: Decimal
    receivables: Decimal
    positions: dict[str, PositionView]
    reconciliation: Literal["MATCHED", "PENDING", "MISMATCH"]
    risk_state: Literal["RUNNING", "REVIEW", "HALTED"]
    event_hash: str
    pending_orders: list[dict[str, Any]] = Field(default_factory=list)


class ExperimentSpec(Contract):
    strategy: Literal["combined", "etf_trend", "stock_lowvol", "buy_hold", "stock_equal"]
    snapshot_id: str
    start: date
    end: date
    capital: Decimal = Field(gt=0)
    seed: int = 0
    cost_multiplier: Decimal = Field(default=Decimal(1), gt=0)
    delay_days: int = Field(default=1, ge=1, le=3)
    config_hash: str
    split: Literal["synthetic", "development", "validation", "unlocked_holdout"]
