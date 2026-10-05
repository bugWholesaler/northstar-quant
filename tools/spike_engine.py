from datetime import date
from pathlib import Path

from rqalpha import run_func
from rqalpha.environment import Environment

from ashare.adapters.backtest.rqalpha_data import rq_symbol
from ashare.config import Fees
from ashare.data.snapshot import publish
from ashare.data.synthetic import generate
from ashare.domain.models import canonical

root = Path("runtime/spike")
tables = generate(root / "csv")
snapshot = publish(root, tables, "synthetic", "synthetic_sample", "historical_assumption")
trades, daily, effective = [], [], []


def init(context):
    from rqalpha.api import subscribe
    from rqalpha.core.events import EVENT

    subscribe([rq_symbol("600000.SH"), rq_symbol("510300.SH")])

    def trade(event):
        t = event.trade
        trades.append(
            {
                "date": str(t.trading_datetime.date()),
                "symbol": t.order_book_id,
                "side": str(t.side),
                "quantity": t.last_quantity,
                "price": t.last_price,
                "fees": t.transaction_cost,
            }
        )

    Environment.get_instance().event_bus.add_listener(EVENT.TRADE, trade)


def handle_bar(context, bars):
    from rqalpha.api import LimitOrder, order_shares

    if context.now.date() == date(2025, 4, 30):
        context.pending = True
    if context.now.date() == date(2025, 5, 2):
        order_shares(
            rq_symbol("600000.SH"), 200, LimitOrder(bars[rq_symbol("600000.SH")].close * 1.02)
        )
        order_shares(
            rq_symbol("510300.SH"), 200, LimitOrder(bars[rq_symbol("510300.SH")].close * 1.02)
        )
        # ETF as well as stock must reject same-day sell.
        order_shares(
            rq_symbol("600000.SH"), -100, LimitOrder(bars[rq_symbol("600000.SH")].close * 0.98)
        )
        order_shares(
            rq_symbol("510300.SH"), -100, LimitOrder(bars[rq_symbol("510300.SH")].close * 0.98)
        )
    if context.now.date() == date(2025, 5, 5):
        order_shares(
            rq_symbol("600000.SH"), -100, LimitOrder(bars[rq_symbol("600000.SH")].close * 0.98)
        )
        order_shares(
            rq_symbol("510300.SH"), -100, LimitOrder(bars[rq_symbol("510300.SH")].close * 0.98)
        )


def after_trading(context):
    effective.append(str(Environment.get_instance().config.mod.sys_simulation.matching_type))
    daily.append(
        {
            "date": str(context.now.date()),
            "cash": context.portfolio.cash,
            "nav": context.portfolio.total_value,
            "positions": {
                p.order_book_id: p.quantity for p in context.portfolio.positions.values()
            },
        }
    )


config = {
    "base": {
        "start_date": "2025-04-29",
        "end_date": "2025-05-06",
        "frequency": "1d",
        "accounts": {"stock": 10000},
        "rqdatac_uri": "disabled",
        "auto_update_bundle": False,
    },
    "extra": {"log_level": "error"},
    "mod": {
        "sys_analyser": {"enabled": False},
        "sys_transaction_cost": {"enabled": False},
        "sys_accounts": {
            "cash_return_by_stock_delisted": False,
            "dividend_reinvestment": False,
            "stock_t1": True,
            "auto_switch_order_value": False,
        },
        "sys_simulation": {
            "matching_type": "next_bar",
            "price_limit": True,
            "volume_limit": True,
            "volume_percent": 0.005,
            "slippage": 0,
        },
        "northstar": {
            "enabled": True,
            "lib": "ashare.adapters.backtest.rqalpha_mod",
            "priority": 100,
            "root": str(root),
            "snapshot_id": snapshot.snapshot_id,
            "fees": Fees().model_dump(mode="json"),
            "cost_multiplier": "1",
        },
    },
}
run_func(init=init, handle_bar=handle_bar, after_trading=after_trading, config=config)

print(
    canonical(
        {
            "trades": trades,
            "daily": daily,
            "requested_matching_type": config["mod"]["sys_simulation"]["matching_type"],
            "effective_matching_type": effective[-1],
            "engine": "RQAlpha 6.1.5",
            "data_scope": "synthetic_sample",
            "bundle": "unused",
        }
    )
)
assert len(trades) == 4, trades
assert all(t["date"] in ("2025-05-02", "2025-05-05") for t in trades)
