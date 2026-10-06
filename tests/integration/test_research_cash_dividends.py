from datetime import date
from decimal import Decimal

import pytest

from ashare.adapters.backtest.rqalpha_engine import RQAlphaEngine
from ashare.config import Config
from ashare.data.free_backtest_snapshot import scenario_limits
from ashare.data.snapshot import publish
from ashare.domain.errors import AshareError, Unsupported
from ashare.domain.models import CorporateAction, ExperimentSpec, Mode, at, stable_id


def scenario_config(tax="0.2"):
    return Config(
        mode=Mode.RESEARCH,
        account_id="research",
        scope="real_sample",
        engine_use="noncommercial_research",
        backtest_profile="research_scenario",
        research_dividend_tax=Decimal(tax),
        stock_count=1,
        listing_days=0,
        stock_window=2,
        liquidity_window=2,
        etf_universe=[],
    )


def cash_market(tmp_path, synthetic, record="2025-05-02", ex="2025-05-05", pay="2025-05-06"):
    symbol = "600000.SH"
    bars = []
    for b in synthetic["bars"]:
        if b.instrument_id != symbol:
            continue
        if b.trade_date >= date.fromisoformat(ex):
            b = b.model_copy(
                update={
                    key: getattr(b, key) - Decimal("0.06")
                    for key in ("open", "high", "low", "close")
                }
                | {"amount": (b.close - Decimal("0.06")) * b.volume}
            )
        bars.append(b)
    action = CorporateAction(
        action_id="fractional-cash",
        instrument_id=symbol,
        kind="dividend",
        record_date=record,
        ex_date=ex,
        pay_date=pay,
        cash_per_share=Decimal("0.0621"),
        published_at=at(date(2025, 4, 29)),
        observed_at=at(date(2025, 4, 29)),
        available_at=at(date(2025, 4, 29)),
    )
    tables = {
        "bars": bars,
        "calendar": synthetic["calendar"],
        "instruments": [i for i in synthetic["instruments"] if i.symbol == symbol],
        "actions": [action],
    }
    snap = publish(
        tmp_path,
        tables,
        "fixture",
        "real_sample",
        "historical_assumption",
        profile="research_scenario",
    )
    return snap, tables


@pytest.mark.parametrize("tax", ["0", "0.2"])
def test_cash_record_ex_payment_and_cent_rounding_match_engine(tmp_path, synthetic, tax):
    cfg = scenario_config(tax)
    snapshot, tables = cash_market(tmp_path, synthetic)
    spec = ExperimentSpec(
        strategy="stock_lowvol",
        snapshot_id=snapshot.snapshot_id,
        start=date(2025, 4, 29),
        end=date(2025, 5, 6),
        capital=Decimal(200000),
        config_hash=stable_id(cfg),
        split="development",
    )
    result = RQAlphaEngine().evaluate(spec, snapshot, tables, cfg, tmp_path)
    assert result["fills"]
    ent = [e for e in result["events"] if e["kind"] == "DividendEntitled"]
    paid = [e for e in result["events"] if e["kind"] == "DividendPaid"]
    assert len(ent) == len(paid) == 1
    assert ent[0]["event_time"].startswith("2025-05-05") and paid[0]["event_time"].startswith(
        "2025-05-06"
    )
    assert Decimal(ent[0]["payload"]["amount"]) == Decimal(paid[0]["payload"]["amount"])
    assert all(Decimal(row["nav_delta"]) < Decimal("0.000001") for row in result["comparisons"])


def test_nonadjacent_record_ex_is_unsupported(tmp_path, synthetic):
    cfg = scenario_config()
    snapshot, tables = cash_market(tmp_path, synthetic, record="2025-04-30")
    spec = ExperimentSpec(
        strategy="stock_lowvol",
        snapshot_id=snapshot.snapshot_id,
        start=date(2025, 4, 29),
        end=date(2025, 5, 6),
        capital=Decimal(200000),
        config_hash=stable_id(cfg),
        split="development",
    )
    with pytest.raises(Unsupported, match="adjacent"):
        RQAlphaEngine().evaluate(spec, snapshot, tables, cfg, tmp_path)


def test_scenario_and_research_usage_cannot_enter_paper_manual():
    from pydantic import ValidationError

    for mode in (Mode.PAPER, Mode.MANUAL):
        with pytest.raises(ValidationError):
            Config(
                mode=mode,
                account_id=mode.value,
                availability="observed",
                backtest_profile="research_scenario",
            )
        with pytest.raises(ValidationError):
            Config(
                mode=mode,
                account_id=mode.value,
                availability="observed",
                engine_use="noncommercial_research",
            )


def test_rule_scenario_distinguishes_etf_and_st_dates():
    assert scenario_limits(
        Decimal(10), Decimal("0.01"), "stock", "600000.SH", True, date(2026, 6, 30)
    ) == (Decimal("10.50"), Decimal("9.50"))
    assert scenario_limits(
        Decimal(10), Decimal("0.01"), "stock", "600000.SH", True, date(2026, 7, 6)
    ) == (Decimal("11.00"), Decimal("9.00"))
    assert scenario_limits(
        Decimal(1), Decimal("0.001"), "etf", "159915.SZ", False, date(2026, 1, 1)
    ) == (Decimal("1.200"), Decimal("0.800"))


def test_duplicate_dividend_proposal_and_final_notice_merge_once():
    from ashare.data.free_backtest_snapshot import cash_actions

    fields = {
        "dividOperateDate": "2026-06-10",
        "dividRegistDate": "2026-06-09",
        "dividPayDate": "2026-06-10",
        "dividCashPsBeforeTax": "1.75",
        "dividStocksPs": "0",
        "dividReserveToStockPs": "",
        "dividPlanAnnounceDate": "2026-03-27",
    }
    actions = cash_actions(
        "601318.SH",
        [{**fields, "dividPlanDate": ""}, {**fields, "dividPlanDate": "2026-06-02"}],
        at(date(2026, 10, 6)),
    )
    assert len(actions) == 1 and actions[0].published_at.date() == date(2026, 6, 2)
    with pytest.raises(AshareError):
        cash_actions(
            "601318.SH",
            [
                {**fields, "dividPlanDate": "2026-06-02"},
                {**fields, "dividPlanDate": "2026-06-02", "dividPayDate": "2026-06-11"},
            ],
            at(date(2026, 10, 6)),
        )
