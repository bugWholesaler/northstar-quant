from datetime import date
from decimal import Decimal
from statistics import median, stdev

from ashare.domain.models import TargetPortfolio, at, stable_id
from ashare.ports.interfaces import MarketDataView


def target(view, day, weights, reasons, strategy, days=3, status="REBALANCE"):
    future = view.next_days(day, days)
    fields = dict(
        strategy_id=strategy,
        decision_at=at(day),
        trade_not_before=future[0],
        expires_at=future[-1],
        snapshot_id=view.snapshot.snapshot_id,
        weights=weights,
        reasons=reasons,
        status=status,
    )
    return TargetPortfolio(target_id=stable_id(fields), **fields)


class ETFTrend:
    def __init__(self, config):
        self.config = config

    def generate(self, view: MarketDataView, day: date):
        if not view.is_month_end(day):
            return target(view, day, {}, {}, "etf_trend", self.config.target_days, "NO_REBALANCE")
        candidates = []
        for symbol in self.config.etf_universe:
            if symbol not in view.instruments or view.instruments[symbol].asset_type != "etf":
                continue
            bars = view.complete_window(symbol, self.config.etf_window + 1)
            if not bars:
                continue
            last = bars[-1]
            if last.st or last.delisting_period:
                continue
            value = last.close * last.factor / (bars[0].close * bars[0].factor) - 1
            if value > 0:
                candidates.append((value, symbol))
        selected = sorted(candidates, key=lambda x: (-x[0], x[1]))[:2]
        return target(
            view,
            day,
            {s: self.config.risk.etf_weight for _, s in selected},
            {s: f"{self.config.etf_window}d adjusted momentum={v}" for v, s in selected},
            "etf_trend",
            self.config.target_days,
        )


class StockLowVol:
    def __init__(self, config):
        self.config = config

    def candidates(self, view, day):
        candidates = []
        for symbol, inst in view.instruments.items():
            if (
                inst.asset_type != "stock"
                or inst.listed_on > day
                or (inst.delisted_on and day >= inst.delisted_on)
            ):
                continue
            age = len([d for d in view.open_days(inst.exchange) if inst.listed_on <= d <= day])
            if age < self.config.listing_days:
                continue
            bars = view.complete_window(
                symbol, max(self.config.stock_window + 1, self.config.liquidity_window)
            )
            if not bars or bars[-1].st or bars[-1].delisting_period:
                continue
            liquidity = median(b.amount for b in bars[-self.config.liquidity_window :])
            prices = [b.close * b.factor for b in bars[-self.config.stock_window - 1 :]]
            volatility = Decimal(str(stdev(float(b / a - 1) for a, b in zip(prices, prices[1:]))))
            candidates.append((liquidity, volatility, symbol))
        liquid = sorted(candidates, key=lambda x: (-x[0], x[2]))[: self.config.liquidity_top]
        return liquid

    def generate(self, view: MarketDataView, day: date):
        if not view.is_month_end(day):
            return target(
                view, day, {}, {}, "stock_lowvol", self.config.target_days, "NO_REBALANCE"
            )
        liquid = self.candidates(view, day)
        selected = sorted(liquid, key=lambda x: (x[1], x[2]))[: self.config.stock_count]
        return target(
            view,
            day,
            {s: self.config.risk.stock_weight for _, _, s in selected},
            {
                s: f"{self.config.stock_window}d volatility={v}; {self.config.liquidity_window}d median turnover={liquidity}"
                for liquidity, v, s in selected
            },
            "stock_lowvol",
            self.config.target_days,
        )


class StockEqualWeight(StockLowVol):
    def generate(self, view, day):
        if not view.is_month_end(day):
            return target(view, day, {}, {}, "stock_equal", self.config.target_days, "NO_REBALANCE")
        liquid = self.candidates(view, day)
        budget = min(
            self.config.risk.stock_weight * self.config.stock_count, self.config.risk.total_weight
        )
        weight = min(self.config.risk.stock_weight, budget / len(liquid)) if liquid else Decimal(0)
        return target(
            view,
            day,
            {symbol: weight for _, _, symbol in liquid},
            {
                symbol: "same historical liquidity-selected stock universe; equal weight"
                for _, _, symbol in liquid
            },
            "stock_equal",
            self.config.target_days,
        )


class BuyHold:
    def __init__(self, config):
        self.config = config

    def generate(self, view, day):
        eligible = sorted(s for s in self.config.etf_universe if view.bars(s, 1))
        weight = (
            min(self.config.risk.etf_weight, self.config.risk.etf_weight * 2 / len(eligible))
            if eligible
            else Decimal(0)
        )
        return target(
            view,
            day,
            {s: weight for s in eligible},
            {s: "fixed-list passive benchmark" for s in eligible},
            "buy_hold",
            self.config.target_days,
        )


def aggregate(targets):
    active = [t for t in targets if t.status == "REBALANCE"]
    if not active:
        return None
    weights, reasons = {}, {}
    for t in active:
        for symbol, weight in t.weights.items():
            weights[symbol] = weights.get(symbol, Decimal(0)) + weight
            reasons[symbol] = t.reasons[symbol]
    first = active[0]
    return first.model_copy(
        update={
            "target_id": stable_id([t.target_id for t in active]),
            "strategy_id": "combined",
            "weights": weights,
            "reasons": reasons,
        }
    )
