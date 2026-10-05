from datetime import datetime

import numpy as np
import pandas as pd
from rqalpha.const import TRADING_CALENDAR_TYPE
from rqalpha.interface import AbstractDataSource
from rqalpha.model.instrument import Instrument as RQInstrument

from ashare.domain.errors import Unsupported


def rq_symbol(symbol):
    return symbol.replace(".SH", ".XSHG").replace(".SZ", ".XSHE")


def canonical_symbol(symbol):
    return symbol.replace(".XSHG", ".SH").replace(".XSHE", ".SZ")


class CanonicalDataSource(AbstractDataSource):
    def __init__(self, snapshot, tables):
        self.snapshot, self.tables = snapshot, tables
        self.instruments = {}
        for inst in tables["instruments"]:
            rq = RQInstrument(
                {
                    "order_book_id": rq_symbol(inst.symbol),
                    "symbol": inst.name,
                    "type": "CS" if inst.asset_type == "stock" else "ETF",
                    "exchange": inst.exchange,
                    "board_type": "MainBoard",
                    "round_lot": inst.lot,
                    "market_tplus": 1,
                    "listed_date": str(inst.listed_on),
                    "de_listed_date": str(inst.delisted_on) if inst.delisted_on else "2999-12-31",
                    "contract_multiplier": 1,
                }
            )
            self.instruments[rq.order_book_id] = rq
        self.bars = {(rq_symbol(b.instrument_id), b.trade_date): b for b in tables["bars"]}

    def get_instruments(self, id_or_syms=None, types=None):
        return [
            i
            for s, i in self.instruments.items()
            if (id_or_syms is None or s in id_or_syms or i.symbol in id_or_syms)
            and (types is None or i.type in types)
        ]

    def get_trading_calendars(self):
        sh = sorted(
            c.trade_date for c in self.tables["calendar"] if c.exchange == "XSHG" and c.is_open
        )
        sz = sorted(
            c.trade_date for c in self.tables["calendar"] if c.exchange == "XSHE" and c.is_open
        )
        if sh != sz:
            raise Unsupported("RQAlpha common calendar requires matching SH/SZ dates")
        return {TRADING_CALENDAR_TYPE.CN_STOCK: pd.DatetimeIndex(sh)}

    def available_data_range(self, frequency):
        if frequency != "1d":
            raise Unsupported("only 1d supported")
        return self.snapshot.start, self.snapshot.end

    def get_bar(self, instrument, dt, frequency):
        if frequency != "1d":
            raise Unsupported("only 1d supported")
        day = dt.date() if hasattr(dt, "date") else dt
        bar = self.bars.get((instrument.order_book_id, day))
        if bar is None:
            return None
        return {
            "datetime": int(day.strftime("%Y%m%d")) * 1000000,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": bar.volume,
            "total_turnover": float(bar.amount),
            "limit_up": float(bar.limit_up),
            "limit_down": float(bar.limit_down),
        }

    def history_bars(
        self,
        instrument,
        bar_count,
        frequency,
        fields,
        dt,
        skip_suspended=True,
        include_now=False,
        adjust_type="pre",
        adjust_orig=None,
    ):
        if frequency != "1d":
            raise Unsupported("only daily history supported")
        day = dt.date() if hasattr(dt, "date") else dt
        rows = [
            b
            for (s, d), b in self.bars.items()
            if s == instrument.order_book_id
            and d <= day
            and (not skip_suspended or not b.suspended)
        ]
        rows.sort(key=lambda b: b.trade_date)
        if bar_count:
            rows = rows[-bar_count:]
        dtype = [
            ("datetime", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("volume", "f8"),
            ("total_turnover", "f8"),
        ]
        array = np.array(
            [
                tuple(
                    self.get_bar(
                        instrument, datetime.combine(b.trade_date, datetime.min.time()), "1d"
                    )[k]
                    for k, _ in dtype
                )
                for b in rows
            ],
            dtype=dtype,
        )
        return array[fields] if fields is not None else array

    def is_suspended(self, order_book_id, dates):
        return [
            self.bars.get((order_book_id, d.date() if hasattr(d, "date") else d)) is None
            or self.bars[order_book_id, d.date() if hasattr(d, "date") else d].suspended
            for d in dates
        ]

    def is_st_stock(self, order_book_id, dates):
        return [
            self.bars[order_book_id, d.date() if hasattr(d, "date") else d].st
            if (order_book_id, d.date() if hasattr(d, "date") else d) in self.bars
            else False
            for d in dates
        ]

    def get_dividend(self, instrument):
        return None  # engine admission blocks snapshots with corporate actions

    def get_split(self, instrument):
        return None

    def get_share_transformation(self, order_book_id):
        return None

    def get_yield_curve(self, start_date, end_date, tenor=None):
        raise Unsupported("no yield-curve data; metrics use explicitly zero risk-free rate")
