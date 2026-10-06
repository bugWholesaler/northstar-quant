from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from ashare.domain.models import Contract, Mode


class Fees(Contract):
    version: str = "scenario-v1"
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0)
    stock_transfer_rate: Decimal = Field(default=Decimal("0.00001"), ge=0)
    stamp_before_20230828: Decimal = Field(default=Decimal("0.001"), ge=0)
    stamp_since_20230828: Decimal = Field(default=Decimal("0.0005"), ge=0)
    verified: bool = False


class Risk(Contract):
    version: str = "paper-example-v1"
    total_weight: Decimal = Field(default=Decimal("0.8"), gt=0, le=1)
    etf_weight: Decimal = Field(default=Decimal("0.2"), gt=0, le=1)
    stock_weight: Decimal = Field(default=Decimal("0.08"), gt=0, le=1)
    liquidity_fraction: Decimal = Field(default=Decimal("0.005"), gt=0, le=1)
    cash_reserve: Decimal = Field(default=Decimal("0.2"), ge=0, lt=1)
    price_protection: Decimal = Field(default=Decimal("0.02"), ge=0, le=Decimal("0.1"))
    drawdown_review: Decimal = Field(default=Decimal("0.1"), gt=0, lt=1)
    approved: bool = False


class BaoStockSettings(Contract):
    start: date = date(2025, 1, 1)
    end: date = date(2026, 9, 30)
    etf_start: date | None = None
    etf_price_mode: Literal["verified_adjusted", "unadjusted_proxy"] = "verified_adjusted"
    symbols: list[str] = Field(default_factory=list)
    timeout: int = Field(default=30, ge=1, le=120)
    refresh: bool = False

    @model_validator(mode="after")
    def dates(self):
        if self.start > self.end:
            raise ValueError("BaoStock start follows end")
        if self.etf_start and not self.start <= self.etf_start <= self.end:
            raise ValueError("ETF start must be within registered range")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("duplicate BaoStock symbols")
        return self


class Config(Contract):
    mode: Mode = Mode.PAPER
    account_id: str = "paper"
    runtime: str = "runtime"
    provider: Literal["csv", "tushare", "baostock"] = "csv"
    csv_directory: str | None = None
    baostock: BaoStockSettings = Field(default_factory=BaoStockSettings)
    scope: Literal["synthetic_sample", "real_sample", "historical_full"] = "real_sample"
    availability: Literal["historical_assumption", "observed"] = "historical_assumption"
    initial_cash: Decimal = Field(default=Decimal("200000"), gt=0)
    fees: Fees = Field(default_factory=Fees)
    risk: Risk = Field(default_factory=Risk)
    etf_universe: list[str] = Field(default_factory=lambda: ["510300.SH", "510500.SH", "159915.SZ"])
    etf_window: int = Field(default=120, ge=1)
    stock_window: int = Field(default=60, ge=2)
    listing_days: int = Field(default=252, ge=0)
    liquidity_window: int = Field(default=20, ge=1)
    liquidity_top: int = Field(default=300, ge=1)
    stock_count: int = Field(default=5, ge=1)
    target_days: int = Field(default=3, ge=1, le=10)
    volume_fraction: Decimal = Field(default=Decimal("0.005"), gt=0, le=1)
    slippage_bps: Decimal = Field(default=Decimal(5), ge=0)
    fixed_annual_cost: Decimal = Field(default=Decimal(0), ge=0)
    broker_mapping_verified: bool = False
    broker_confirmed: bool = False
    personal_investment_engine_license_confirmed: bool = False
    strategy_approved: bool = False

    @model_validator(mode="after")
    def isolation(self):
        if self.account_id != self.mode.value:
            raise ValueError("v1 account id must match isolated mode database")
        if self.mode == Mode.PAPER and self.availability != "observed":
            raise ValueError("paper requires actual observation timestamps")
        return self

    def manual_blockers(self):
        if self.mode != Mode.MANUAL:
            return []
        return [
            name
            for name, ok in {
                "actual_fee_schedule": self.fees.verified
                and {
                    "version",
                    "commission_rate",
                    "minimum_commission",
                    "stock_transfer_rate",
                    "stamp_before_20230828",
                    "stamp_since_20230828",
                }
                <= self.fees.model_fields_set
                and not self.fees.version.lower().startswith(("required", "scenario", "paper")),
                "personal_risk_budget": self.risk.approved
                and {
                    "version",
                    "total_weight",
                    "etf_weight",
                    "stock_weight",
                    "liquidity_fraction",
                    "cash_reserve",
                    "price_protection",
                    "drawdown_review",
                }
                <= self.risk.model_fields_set
                and not self.risk.version.lower().startswith(("required", "paper", "scenario")),
                "broker_mapping": self.broker_mapping_verified,
                "broker_confirmation": self.broker_confirmed,
                "strategy_version_approval": self.strategy_approved,
            }.items()
            if not ok
        ]


def load_config(path: str | Path) -> Config:
    return Config.model_validate(yaml.safe_load(Path(path).read_text()))
