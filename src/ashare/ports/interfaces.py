from datetime import date, datetime
from typing import Protocol

from ashare.domain.models import ExecutionEvent, ExperimentSpec, TargetPortfolio


class DataProvider(Protocol):
    def fetch(self, dataset: str, start: date, end: date, cursor: int = 0) -> dict: ...


class MarketDataView(Protocol):
    decision_at: datetime
    snapshot: object
    instruments: dict

    def bars(self, instrument_id: str, count: int): ...
    def complete_window(self, instrument_id: str, count: int): ...
    def open_days(self, exchange: str = "XSHG"): ...
    def next_days(self, day: date, count: int): ...
    def is_month_end(self, day: date): ...


class Strategy(Protocol):
    def generate(self, view: MarketDataView, day: date) -> TargetPortfolio: ...


class BacktestEngine(Protocol):
    def evaluate(
        self, spec: ExperimentSpec, snapshot, tables, config, root, frozen_plans=None
    ) -> dict: ...


class LedgerStore(Protocol):
    def append(self, events: list[ExecutionEvent]) -> int: ...


class Registry:
    def __init__(self):
        self._classes = {}

    def register(self, name, cls):
        if name in self._classes:
            raise ValueError(f"duplicate adapter: {name}")
        self._classes[name] = cls

    def resolve(self, name):
        from ashare.domain.errors import Unsupported

        try:
            return self._classes[name]
        except KeyError:
            raise Unsupported("adapter is not registered", adapter=name) from None
