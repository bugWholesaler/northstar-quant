"""Deterministic engineering fixtures; calendar and prices are invented."""

import csv
import math
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ashare.data.snapshot import TABLES
from ashare.domain.models import Bar, CalendarDay, Instrument, at


def generate(directory: Path, observed=False):
    directory.mkdir(parents=True, exist_ok=True)
    first, last = date(2024, 1, 1), date(2025, 5, 12)
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    symbols = [
        ("510300.SH", "etf"),
        ("510500.SH", "etf"),
        ("159915.SZ", "etf"),
        ("600000.SH", "stock"),
        ("600001.SH", "stock"),
        ("600002.SH", "stock"),
        ("000001.SZ", "stock"),
        ("000002.SZ", "stock"),
        ("000003.SZ", "stock"),
    ]
    instruments = [
        Instrument(
            instrument_id=s,
            symbol=s,
            exchange="XSHG" if s.endswith("SH") else "XSHE",
            asset_type=t,
            name=f"SYNTHETIC {s}",
            board="MAIN" if t == "stock" else "EQUITY_ETF",
            listed_on=first,
            tick=Decimal("0.01") if t == "stock" else Decimal("0.001"),
        )
        for s, t in symbols
    ]
    calendar = [
        CalendarDay(exchange=e, trade_date=d, is_open=d.weekday() < 5 and d != date(2025, 5, 1))
        for d in days
        for e in ("XSHG", "XSHE")
    ]
    trading = [
        c.trade_date
        for c in calendar
        if c.exchange == "XSHG" and c.is_open and c.trade_date <= date(2025, 5, 6)
    ]
    bars = []
    for index, instrument in enumerate(instruments):
        previous = Decimal(10)
        for n, day in enumerate(trading):
            tick = instrument.tick
            close = (
                Decimal(
                    str(
                        10
                        + n * (0.005 + index * 0.0007)
                        + math.sin(n / (7 + index)) * 0.04 * (index + 1)
                    )
                )
                / tick
            ).quantize(Decimal(1)) * tick
            bars.append(
                Bar(
                    instrument_id=instrument.instrument_id,
                    trade_date=day,
                    open=close,
                    high=close + tick * 30,
                    low=close - tick * 30,
                    close=close,
                    volume=5000000,
                    amount=close * 5000000,
                    factor=Decimal(1),
                    limit_up=(previous * Decimal("1.1") / tick).quantize(Decimal(1)) * tick,
                    limit_down=(previous * Decimal("0.9") / tick).quantize(Decimal(1)) * tick,
                    available_at=at(day),
                    observed_at=at(day) if observed else at(date(2026, 10, 5)),
                    availability_basis="observed" if observed else "historical_assumption",
                )
            )
            previous = close
    tables = {"instruments": instruments, "calendar": calendar, "bars": bars, "actions": []}
    for name, models in tables.items():
        fields = list(TABLES[name].model_fields)
        with (directory / f"{name}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for model in models:
                writer.writerow(
                    {
                        k: ("" if v is None else str(v).lower() if isinstance(v, bool) else v)
                        for k, v in model.model_dump(mode="json").items()
                    }
                )
    return tables
