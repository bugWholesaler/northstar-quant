from __future__ import annotations

import tempfile
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from ashare.accounting.ledger import Ledger, new_event
from ashare.data.snapshot import MarketView
from ashare.domain.errors import AshareError, Unsupported
from ashare.domain.models import Mode, OrderIntent, at, decimal, money
from ashare.portfolio.planner import ensure_price_quantity, plan
from ashare.strategies.rules import BuyHold, ETFTrend, StockEqualWeight, StockLowVol, aggregate

from .rqalpha_data import canonical_symbol, rq_symbol


def adverse_price(bar, instrument, side, bps):
    value = bar.close * (1 + bps / 10000 if side == "BUY" else 1 - bps / 10000)
    return (value / instrument.tick).to_integral_value(
        rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    ) * instrument.tick


def matching_config(config, spec, root):
    return {
        "base": {
            "start_date": str(spec.start),
            "end_date": str(spec.end),
            "frequency": "1d",
            "accounts": {"stock": float(spec.capital)},
            "rqdatac_uri": "disabled",
            "auto_update_bundle": False,
            "round_price": False,
            "forced_liquidation": False,
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
                "dividend_tax_rate": float(config.research_dividend_tax)
                if config.backtest_profile == "research_scenario"
                else 0,
            },
            "sys_simulation": {
                "matching_type": "current_bar",
                "price_limit": True,
                "volume_limit": True,
                "volume_percent": float(config.volume_fraction),
                "inactive_limit": True,
                "slippage_model": "ashare.adapters.backtest.rqalpha_engine.CanonicalSlippage",
                "slippage": float(config.slippage_bps / 10000),
            },
            "northstar": {
                "enabled": True,
                "lib": "ashare.adapters.backtest.rqalpha_mod",
                "priority": 100,
                "root": str(root),
                "snapshot_id": spec.snapshot_id,
                "fees": config.fees.model_dump(mode="json"),
                "cost_multiplier": str(spec.cost_multiplier),
            },
        },
    }


# RQAlpha creates this class inside its matcher. The pre-submit gate checks the
# adverse price against the bar, exchange limits and the frozen order limit.
class CanonicalSlippage:
    def __init__(self, rate):
        self.rate = rate

    def get_trade_price(self, order, price):
        from rqalpha.const import SIDE
        from rqalpha.environment import Environment

        env = Environment.get_instance()
        symbol = canonical_symbol(order.order_book_id)
        source = env.data_source
        bar = source.bars[order.order_book_id, env.trading_dt.date()]
        inst = next(i for i in source.tables["instruments"] if i.symbol == symbol)
        return float(
            adverse_price(
                bar, inst, "BUY" if order.side == SIDE.BUY else "SELL", decimal(self.rate) * 10000
            )
        )


class RQAlphaEngine:
    version = "6.1.5"

    def evaluate(
        self, spec, snapshot, tables, config, root: Path, frozen_plans: dict | None = None
    ):
        from rqalpha import __version__, run_func
        from rqalpha.api import LimitOrder, order_shares, subscribe
        from rqalpha.core.events import EVENT
        from rqalpha.environment import Environment

        if __version__ != self.version:
            raise AshareError("ENGINE_VERSION", "unexpected engine version")
        if config.mode == Mode.MANUAL:
            raise AshareError("MODE_ISOLATION", "simulation cannot run in manual account")
        if (
            snapshot.scope != "synthetic_sample"
            and not config.personal_investment_engine_license_confirmed
            and not (config.mode == Mode.RESEARCH and config.engine_use == "noncommercial_research")
        ):
            raise AshareError(
                "ENGINE_LICENSE",
                "real-data investment use requires license applicability confirmation",
            )
        if snapshot.profile != config.backtest_profile:
            raise AshareError("SNAPSHOT_PROFILE", "config and snapshot execution profile differ")
        scenario = snapshot.profile == "research_scenario"
        if scenario and (config.mode != Mode.RESEARCH or frozen_plans is not None):
            raise AshareError(
                "MODE_ISOLATION", "scenario snapshots are only for retrospective research"
            )
        if tables["actions"] and not scenario:
            raise Unsupported(
                "corporate-action engine parity is not yet verified; run INVALID, no performance published"
            )
        if scenario:
            days = sorted(
                c.trade_date for c in tables["calendar"] if c.exchange == "XSHG" and c.is_open
            )
            for action in tables["actions"]:
                if action.kind != "dividend" or not action.implemented or action.bonus_ratio:
                    raise Unsupported("scenario supports implemented cash dividends only")
                before = [d for d in days if d < action.ex_date]
                if (
                    not before
                    or before[-1] != action.record_date
                    or action.pay_date < action.ex_date
                ):
                    raise Unsupported(
                        "cash-dividend record/ex/pay timing requires verified adjacent calendar dates"
                    )
        if any(i.delisted_on and i.delisted_on <= spec.end for i in tables["instruments"]):
            raise Unsupported(
                "delisting engine/ledger parity not verified; no automatic cash return"
            )
        if not snapshot.start <= spec.start <= spec.end <= snapshot.end:
            raise AshareError("DATA_RANGE", "experiment range outside frozen snapshot")
        mode = config.mode
        directory = tempfile.TemporaryDirectory(prefix="northstar-engine-")
        ledger = Ledger(Path(directory.name) / "ledger.sqlite", config.account_id, mode)
        ledger.append(
            [
                new_event(
                    config.account_id,
                    mode,
                    "Deposit",
                    spec.start,
                    {"amount": str(spec.capital)},
                    "engine",
                    key="initial-capital",
                    event_time=at(spec.start, 8, 0),
                )
            ]
        )
        with ledger.db:
            ledger.set_meta("reconciliation", "MATCHED")
        output = {
            "events": [],
            "equity": [],
            "plans": {},
            "targets": [],
            "orders": [],
            "fills": [],
            "comparisons": [],
            "rejections": [],
            "effective": None,
            "requested": matching_config(config, spec, root),
        }
        allbars = {(b.instrument_id, b.trade_date): b for b in tables["bars"]}
        instruments = {i.instrument_id: i for i in tables["instruments"]}
        active_intent, current_target, pending = None, None, None
        engine_orders = []
        trade_counter = {}

        def init(context):
            subscribe([rq_symbol(s) for s in instruments])
            env = Environment.get_instance()

            def trade(event):
                nonlocal active_intent
                t = event.trade
                intent = active_intent
                if intent is None:
                    raise AshareError("UNTRACKED_FILL", "engine fill without frozen intent")
                day = t.trading_datetime.date()
                trade_counter[intent.intent_id] = trade_counter.get(intent.intent_id, 0) + 1
                following = MarketView(snapshot, tables, at(day)).next_days(day, 1)[0]
                payload = {
                    "symbol": intent.instrument_id,
                    "side": intent.side,
                    "quantity": t.last_quantity,
                    "price": str(t.last_price),
                    "gross_amount": str(money(decimal(t.last_price) * t.last_quantity)),
                    "commission": str(money(t.commission)),
                    "tax": str(money(t.tax)),
                    "other_fees": str(money(t.transaction_cost - t.commission - t.tax)),
                    "order_id": intent.intent_id,
                    "sellable_on": str(following),
                    "external": False,
                }
                ledger.append(
                    [
                        new_event(
                            config.account_id,
                            mode,
                            "Fill",
                            day,
                            payload,
                            "rqalpha",
                            key=[intent.intent_id, trade_counter[intent.intent_id]],
                            event_time=at(day, 15, 0),
                        )
                    ]
                )
                output["fills"].append(
                    {**payload, "trade_date": str(day), "intent_id": intent.intent_id}
                )

            env.event_bus.add_listener(EVENT.TRADE, trade)

        def before_trading(context):
            day = context.now.date()
            if not scenario:
                return
            for action in tables["actions"]:
                if action.ex_date == day:
                    position = ledger.view(at(action.record_date)).positions.get(
                        action.instrument_id
                    )
                    quantity = position.quantity if position else 0
                    if quantity:
                        gross = money(quantity * action.cash_per_share)
                        net = money(
                            quantity * action.cash_per_share * (1 - config.research_dividend_tax)
                        )
                        ledger.append(
                            [
                                new_event(
                                    config.account_id,
                                    mode,
                                    "DividendEntitled",
                                    day,
                                    {
                                        "action_id": action.action_id,
                                        "amount": str(net),
                                        "gross_amount": str(gross),
                                        "scenario_tax": str(gross - net),
                                        "quantity": quantity,
                                    },
                                    "corporate_action",
                                    key=[action.action_id, "entitled"],
                                    event_time=at(day, 9, 0),
                                )
                            ]
                        )
                if action.pay_date == day and action.action_id in ledger.state()["receivables"]:
                    ledger.append(
                        [
                            new_event(
                                config.account_id,
                                mode,
                                "DividendPaid",
                                day,
                                {
                                    "action_id": action.action_id,
                                    "amount": ledger.state()["receivables"][action.action_id],
                                },
                                "corporate_action",
                                key=[action.action_id, "paid"],
                                event_time=at(day, 9, 1),
                            )
                        ]
                    )

        def handle_bar(context, bars):
            nonlocal active_intent
            day = context.now.date()
            prior = (frozen_plans or {}).get(str(day)) if frozen_plans is not None else pending
            if not prior or prior["valid_date"] != str(day) or prior["blockers"]:
                return
            for row in prior["intents"]:
                intent = OrderIntent.model_validate(row)
                inst = instruments[intent.instrument_id]
                ensure_price_quantity(intent, inst)
                bar = allbars.get((intent.instrument_id, day))
                price = (
                    adverse_price(bar, inst, intent.side, config.slippage_bps)
                    if bar
                    else Decimal(0)
                )
                reason = None
                if not bar or bar.suspended or bar.volume == 0:
                    reason = "SUSPENDED_OR_MISSING"
                elif intent.side == "BUY" and (bar.st or bar.delisting_period):
                    reason = "BUY_STATUS_BLOCKED"
                elif (
                    not bar.low <= price <= bar.high or not bar.limit_down <= price <= bar.limit_up
                ):
                    reason = "ADVERSE_PRICE_OUTSIDE_MARKET"
                elif (
                    intent.side == "BUY"
                    and (price > intent.limit_price or bar.high >= bar.limit_up)
                ) or (
                    intent.side == "SELL"
                    and (price < intent.limit_price or bar.low <= bar.limit_down)
                ):
                    reason = "PRICE_PROTECTION_OR_LIMIT_TOUCH"
                if reason:
                    output["rejections"].append(
                        {"date": str(day), "intent_id": intent.intent_id, "reason": reason}
                    )
                    continue
                payload = {
                    "order_id": intent.intent_id,
                    "symbol": intent.instrument_id,
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "limit_price": str(intent.limit_price),
                    "fee_reserve": str(intent.estimated_fees),
                    "state": "SUBMITTED",
                    "intent_id": intent.intent_id,
                }
                ledger.append(
                    [
                        new_event(
                            config.account_id,
                            mode,
                            "OrderRegistered",
                            day,
                            payload,
                            "rqalpha",
                            key=[intent.intent_id, "submitted"],
                            event_time=at(day, 14, 59),
                        )
                    ]
                )
                active_intent = intent
                order = order_shares(
                    rq_symbol(intent.instrument_id),
                    intent.quantity if intent.side == "BUY" else -intent.quantity,
                    LimitOrder(float(intent.limit_price)),
                )
                active_intent = None
                engine_orders.append((intent, order))
                output["orders"].append(intent.model_dump(mode="json"))

        def after_trading(context):
            nonlocal current_target, pending
            day = context.now.date()
            env = Environment.get_instance()
            effective = {
                "matching_type": str(env.config.mod.sys_simulation.matching_type),
                "stock_t1": env.config.mod.sys_accounts.stock_t1,
                "cash_return_by_stock_delisted": env.config.mod.sys_accounts.cash_return_by_stock_delisted,
                "volume_percent": env.config.mod.sys_simulation.volume_percent,
                "slippage": env.config.mod.sys_simulation.slippage,
                "price_limit": env.config.mod.sys_simulation.price_limit,
                "volume_limit": env.config.mod.sys_simulation.volume_limit,
                "inactive_limit": env.config.mod.sys_simulation.inactive_limit,
                "dividend_reinvestment": env.config.mod.sys_accounts.dividend_reinvestment,
            }
            if (
                "CURRENT_BAR_CLOSE" not in effective["matching_type"]
                or not effective["stock_t1"]
                or effective["cash_return_by_stock_delisted"]
                or effective["volume_percent"] != float(config.volume_fraction)
                or effective["slippage"] != float(config.slippage_bps / 10000)
                or not effective["price_limit"]
                or not effective["volume_limit"]
                or not effective["inactive_limit"]
                or effective["dividend_reinvestment"]
            ):
                raise AshareError(
                    "ENGINE_CONFIG",
                    "effective settings violate requested execution contract",
                    effective=effective,
                )
            output["effective"] = effective
            for intent, order in list(engine_orders):
                o = ledger.state()["orders"][intent.intent_id]
                if o["filled"] < o["quantity"]:
                    status = "REJECTED" if o["filled"] == 0 else "CANCELLED"
                    ledger.append(
                        [
                            new_event(
                                config.account_id,
                                mode,
                                "OrderRegistered",
                                day,
                                {**o, "state": status},
                                "rqalpha",
                                key=[intent.intent_id, status],
                                event_time=at(day, 16, 0),
                            )
                        ]
                    )
            engine_orders.clear()
            view = MarketView(snapshot, tables, at(day))
            account = ledger.view(at(day))
            engine_positions = {
                canonical_symbol(p.order_book_id): p.quantity
                for p in context.portfolio.get_positions()
                if p.quantity
            }
            actual_positions = {s: p.quantity for s, p in account.positions.items()}
            nav = account.cash_total + account.receivables
            for symbol, position in account.positions.items():
                bars_history = view.bars(symbol, 1)
                if not bars_history:
                    raise AshareError("VALUATION_MISSING", "cannot value position")
                nav += position.quantity * bars_history[-1].close
            delta_cash = abs(decimal(context.portfolio.cash) - account.cash_total)
            delta_nav = abs(decimal(context.portfolio.total_value) - nav)
            if (
                delta_cash > Decimal("0.000001")
                or delta_nav > Decimal("0.000001")
                or engine_positions != actual_positions
            ):
                raise AshareError(
                    "ENGINE_LEDGER_MISMATCH",
                    "daily engine/ledger comparison failed",
                    day=str(day),
                    cash=str(delta_cash),
                    nav=str(delta_nav),
                )
            with ledger.db:
                ledger.set_meta("reconciliation", "MATCHED")
            output["comparisons"].append(
                {
                    "date": str(day),
                    "cash_delta": str(delta_cash),
                    "nav_delta": str(delta_nav),
                    "positions_match": True,
                }
            )
            output["equity"].append(
                {
                    "date": str(day),
                    "nav": str(nav),
                    "cash": str(account.cash_total),
                    "positions": actual_positions,
                }
            )
            account = ledger.view(at(day))
            peak = max(decimal(e["nav"]) for e in output["equity"])
            if peak and 1 - nav / peak >= config.risk.drawdown_review:
                with ledger.db:
                    ledger.set_meta("risk_state", "REVIEW")
            if frozen_plans is not None:
                return
            strategies = {
                "combined": [ETFTrend(config), StockLowVol(config)],
                "etf_trend": [ETFTrend(config)],
                "stock_lowvol": [StockLowVol(config)],
                "buy_hold": [BuyHold(config)],
                "stock_equal": [StockEqualWeight(config)],
                "combined_passive": [BuyHold(config), StockEqualWeight(config)],
            }[spec.strategy]
            if view.is_month_end(day) or (spec.strategy == "buy_hold" and current_target is None):
                targets = [strategy.generate(view, day) for strategy in strategies]
                current_target = aggregate(targets)
                if current_target:
                    output["targets"].append(current_target.model_dump(mode="json"))
            if (
                pending
                and pending["valid_date"] > str(day)
                and current_target
                and pending["target"]["target_id"] == current_target.target_id
            ):
                return  # keep the frozen deferred plan instead of postponing it every day
            pending = None
            if current_target:
                future = view.next_days(day, spec.delay_days)[-1]
                if current_target.trade_not_before <= future <= current_target.expires_at:
                    # Retry uses frozen decision prices and target, settled account facts.
                    decision_view = MarketView(snapshot, tables, current_target.decision_at)
                    pending = plan(
                        current_target, decision_view, ledger.view(at(day)), config, future
                    )
                    output["plans"][str(future)] = pending

        try:
            run_func(
                init=init,
                handle_bar=handle_bar,
                before_trading=before_trading,
                after_trading=after_trading,
                config=output["requested"],
            )
            expected = [
                c.trade_date
                for c in tables["calendar"]
                if c.exchange == "XSHG" and c.is_open and spec.start <= c.trade_date <= spec.end
            ]
            if [e["date"] for e in output["equity"]] != [str(d) for d in expected]:
                raise AshareError("ENGINE_INCOMPLETE", "engine did not process requested dates")
            ledger.replay()
            output["events"] = [e.model_dump(mode="json") for e in ledger.events()]
            output["status"] = "SUCCESS"
            return output
        finally:
            ledger.close()
            directory.cleanup()
