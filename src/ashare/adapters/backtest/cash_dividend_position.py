"""Pinned RQAlpha position extension for declared research cash-dividend scenarios."""

from datetime import date

from rqalpha.const import INSTRUMENT_TYPE
from rqalpha.mod.rqalpha_mod_sys_accounts.position_model import StockPosition
from rqalpha.portfolio.position import POSITION_TYPE_MAP

from ashare.domain.models import decimal, money


def install_cash_dividend_position():
    previous = POSITION_TYPE_MAP[INSTRUMENT_TYPE.CS]

    class RoundedCashDividendPosition(StockPosition):
        __instrument_types__ = [INSTRUMENT_TYPE.CS]

        def _handle_dividend_book_closure(self, trading_date, data_proxy):
            records = self._get_dividends_or_splits(
                self._all_dividends, trading_date, "ex_dividend_date"
            )
            if records is None or not len(records):
                return 0.0
            total = 0.0
            for record in records:
                per_share = decimal(record["dividend_cash_before_tax"]) / decimal(
                    record["round_lot"]
                )
                net_per_share = per_share * (1 - decimal(self.dividend_tax_rate))
                amount = money(self._quantity * net_per_share)
                encoded = str(int(record["payable_date"]))
                payable = date(int(encoded[:4]), int(encoded[4:6]), int(encoded[6:8]))
                self._avg_price -= float(net_per_share)
                self._last_price -= float(net_per_share)
                self._dividend_receivable.append((payable, float(amount)))
                total += float(amount)
            return total

    return previous


def restore_cash_dividend_position(previous):
    POSITION_TYPE_MAP[INSTRUMENT_TYPE.CS] = previous
