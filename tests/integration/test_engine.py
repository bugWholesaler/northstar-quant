from datetime import date
from decimal import Decimal

import pytest

from ashare.accounting.ledger import Ledger, new_event
from ashare.adapters.backtest.rqalpha_engine import RQAlphaEngine
from ashare.data.snapshot import MarketView, publish
from ashare.domain.errors import Unsupported
from ashare.domain.models import ExperimentSpec, OrderIntent, at, stable_id
from ashare.portfolio.planner import plan
from ashare.strategies.rules import ETFTrend


def spec(snapshot, config, **changes):
    values = dict(
        strategy="combined",
        snapshot_id=snapshot.snapshot_id,
        start=date(2025, 4, 29),
        end=date(2025, 5, 6),
        capital=Decimal(200000),
        config_hash=stable_id(config),
        split="synthetic",
    )
    return ExperimentSpec(**(values | changes))


def test_engine_delayed_execution_and_daily_ledger_parity(market, config):
    root, snapshot, tables = market
    result = RQAlphaEngine().evaluate(spec(snapshot, config), snapshot, tables, config, root)
    assert result["status"] == "SUCCESS" and result["fills"]
    assert min(f["trade_date"] for f in result["fills"]) == "2025-05-02"
    assert {
        i.asset_type
        for i in tables["instruments"]
        if any(f["symbol"] == i.symbol for f in result["fills"])
    } == {"etf", "stock"}
    assert all(
        c["positions_match"] and Decimal(c["cash_delta"]) < Decimal("0.000001")
        for c in result["comparisons"]
    )
    assert "CURRENT_BAR_CLOSE" in result["effective"]["matching_type"]
    assert all(f["trade_date"] > "2025-04-30" for f in result["fills"])


def test_future_tenfold_shock_preserves_signal_and_planned_quantity(market, config):
    root, snapshot, tables = market
    engine = RQAlphaEngine()
    original = engine.evaluate(spec(snapshot, config), snapshot, tables, config, root)
    shock = []
    for b in tables["bars"]:
        shock.append(
            b.model_copy(
                update={
                    k: getattr(b, k) * 10
                    for k in ("open", "high", "low", "close", "amount", "limit_up", "limit_down")
                }
            )
            if b.trade_date > date(2025, 4, 30)
            else b
        )
    changed = {**tables, "bars": shock}
    new = publish(root, changed, "synthetic", "synthetic_sample", "historical_assumption")
    result = engine.evaluate(spec(new, config), new, changed, config, root)
    before = original["plans"]["2025-05-02"]["intents"]
    after = result["plans"]["2025-05-02"]["intents"]
    assert [(i["instrument_id"], i["quantity"]) for i in before] == [
        (i["instrument_id"], i["quantity"]) for i in after
    ]
    assert not result["fills"]
    assert result["rejections"]


@pytest.mark.parametrize("status", ["suspended", "limit_up", "limit_down"])
def test_untradeable_markets_do_not_create_ideal_fills(market, config, status):
    root, snapshot, tables = market
    changed = []
    for b in tables["bars"]:
        if b.trade_date >= date(2025, 5, 2):
            if status == "suspended":
                b = b.model_copy(update={"suspended": True, "volume": 0, "amount": Decimal(0)})
            elif status == "limit_up":
                b = b.model_copy(
                    update={
                        "high": b.limit_up,
                        "close": b.limit_up,
                        "open": b.limit_up,
                        "low": b.limit_up,
                        "amount": b.limit_up * b.volume,
                    }
                )
        changed.append(b)
    tables = {**tables, "bars": changed}
    new = publish(root, tables, "synthetic", "synthetic_sample", "historical_assumption")
    if status == "limit_down":
        # Pure sell-side admission case, evaluated independently of strategy.
        from ashare.adapters.backtest.rqalpha_engine import adverse_price

        bar = tables["bars"][0].model_copy(update={"low": tables["bars"][0].limit_down})
        assert bar.low <= bar.limit_down
        assert adverse_price(bar, tables["instruments"][0], "SELL", Decimal(5)) <= bar.close
    else:
        result = RQAlphaEngine().evaluate(spec(new, config), new, tables, config, root)
        assert not result["fills"]


def test_unknown_action_and_delisting_invalidate_run(market, config):
    root, snapshot, tables = market
    from ashare.domain.models import CorporateAction

    action = CorporateAction(
        action_id="unsupported",
        instrument_id="600000.SH",
        kind="unsupported",
        record_date=date(2025, 4, 30),
        ex_date=date(2025, 5, 2),
        pay_date=date(2025, 5, 2),
        published_at=at(date(2025, 4, 29)),
        observed_at=at(date(2025, 4, 29)),
        available_at=at(date(2025, 4, 29)),
    )
    with pytest.raises(Unsupported):
        RQAlphaEngine().evaluate(
            spec(snapshot, config), snapshot, {**tables, "actions": [action]}, config, root
        )
    changed = {
        **tables,
        "instruments": [
            i.model_copy(update={"delisted_on": date(2025, 5, 2)}) if i.symbol == "600000.SH" else i
            for i in tables["instruments"]
        ],
    }
    with pytest.raises(Unsupported):
        RQAlphaEngine().evaluate(spec(snapshot, config), snapshot, changed, config, root)


def test_cash_aggregate_and_sale_proceeds_not_reused(market, config):
    root, snapshot, tables = market
    instance = Ledger(root / "test.sqlite", "demo", config.mode)
    try:
        day = date(2025, 4, 30)
        instance.append(
            [
                new_event(
                    "demo",
                    config.mode,
                    "AccountInitialized",
                    day,
                    {
                        "cash": "1000",
                        "positions": [
                            {
                                "symbol": "600000.SH",
                                "quantity": 1000,
                                "sellable_on": "2025-04-30",
                                "cost": "10000",
                                "acquired": "2025-04-01",
                            }
                        ],
                    },
                    event_time=at(day, 8, 0),
                )
            ]
        )
        with instance.db:
            instance.set_meta("reconciliation", "MATCHED")
        view = MarketView(snapshot, tables, at(day))
        t = ETFTrend(config).generate(view, day)
        result = plan(t, view, instance.view(at(day)), config)
        buys = [i for i in result["intents"] if i["side"] == "BUY"]
        sells = [i for i in result["intents"] if i["side"] == "SELL"]
        assert sells
        assert (
            sum(
                Decimal(i["limit_price"]) * i["quantity"] + Decimal(i["estimated_fees"])
                for i in buys
            )
            <= 1000
        )
    finally:
        instance.close()


def test_price_tick_lot_validation(config):
    from ashare.domain.errors import AshareError
    from ashare.domain.models import Instrument
    from ashare.portfolio.planner import ensure_price_quantity

    inst = Instrument(
        instrument_id="600000.SH",
        symbol="600000.SH",
        exchange="XSHG",
        asset_type="stock",
        name="fixture",
        board="MAIN",
        listed_on=date(2024, 1, 1),
        tick=Decimal("0.01"),
    )
    intent = OrderIntent(
        intent_id="i",
        plan_id="p",
        account_id="demo",
        target_id="t",
        instrument_id=inst.symbol,
        side="BUY",
        quantity=101,
        limit_price=Decimal(10),
        reference_price=Decimal(10),
        valid_date=date(2025, 5, 2),
        estimated_fees=Decimal(5),
        reason="test",
    )
    with pytest.raises(AshareError, match="round lot"):
        ensure_price_quantity(intent, inst)
    ensure_price_quantity(intent.model_copy(update={"side": "SELL"}), inst, 101)
    with pytest.raises(AshareError, match="tick"):
        ensure_price_quantity(
            intent.model_copy(update={"quantity": 100, "limit_price": Decimal("10.001")}), inst
        )


def test_sell_at_limit_down_never_fills(market, config):
    root, snapshot, tables = market
    day = date(2025, 5, 2)
    symbol = "600000.SH"
    modified = []
    for bar in tables["bars"]:
        if bar.trade_date == day and bar.instrument_id == symbol:
            value = bar.limit_down
            bar = bar.model_copy(
                update={
                    "open": value,
                    "high": value,
                    "low": value,
                    "close": value,
                    "amount": value * bar.volume,
                }
            )
        modified.append(bar)
    changed = {**tables, "bars": modified}
    new = publish(root, changed, "synthetic", "synthetic_sample", "historical_assumption")

    def frozen_intent(valid, side, quantity, limit):
        intent = OrderIntent(
            intent_id=side,
            plan_id="p" + side,
            account_id="demo",
            target_id="fixture",
            instrument_id=symbol,
            side=side,
            quantity=quantity,
            limit_price=Decimal(limit),
            reference_price=Decimal("12"),
            valid_date=valid,
            estimated_fees=Decimal(5),
            reason="fixture",
        )
        return {
            "valid_date": str(valid),
            "blockers": [],
            "intents": [intent.model_dump(mode="json")],
        }

    frozen = {
        "2025-04-30": frozen_intent(date(2025, 4, 30), "BUY", 200, "12.50"),
        "2025-05-02": frozen_intent(day, "SELL", 100, "11.00"),
    }
    result = RQAlphaEngine().evaluate(spec(new, config), new, changed, config, root, frozen)
    assert len(result["fills"]) == 1 and result["fills"][0]["side"] == "BUY"
    assert result["equity"][-1]["positions"][symbol] == 200
    assert result["rejections"]


def test_cost_decider_accumulates_minimum_per_order(monkeypatch):
    from types import SimpleNamespace

    from rqalpha.const import INSTRUMENT_TYPE, SIDE
    from rqalpha.environment import Environment

    from ashare.adapters.backtest.rqalpha_mod import ScenarioCostDecider
    from ashare.config import Fees

    monkeypatch.setattr(
        Environment,
        "get_instance",
        staticmethod(lambda: SimpleNamespace(trading_dt=at(date(2025, 5, 2)))),
    )
    decider = ScenarioCostDecider(Fees(), "1")
    instrument = SimpleNamespace(type=INSTRUMENT_TYPE.CS)
    costs = [
        decider.calc(
            SimpleNamespace(
                instrument=instrument, price=10, quantity=quantity, side=SIDE.BUY, order_id=7
            )
        )
        for quantity in (100, 100, 1800)
    ]
    assert [cost.commission for cost in costs] == [5, 0, 1]
    assert sum(Decimal(str(cost.other_fees)) for cost in costs) == Decimal("0.2")


def test_extra_day_delay_executes_frozen_plan_instead_of_chasing_date(market, config):
    root, snapshot, tables = market
    result = RQAlphaEngine().evaluate(
        spec(snapshot, config, delay_days=2), snapshot, tables, config, root
    )
    assert result["fills"]
    assert min(fill["trade_date"] for fill in result["fills"]) == "2025-05-05"
