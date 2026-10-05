from datetime import date

import pytest

from ashare.accounting.ledger import new_event
from ashare.data.snapshot import MarketView
from ashare.demo import write_csv
from ashare.domain.errors import AshareError
from ashare.domain.models import Mode, at
from ashare.execution import plans
from ashare.execution.importer import apply, preview
from ashare.execution.reconcile import reconcile
from ashare.portfolio.planner import plan
from ashare.strategies.rules import ETFTrend

DAY = date(2025, 4, 30)


def fill_row(**changes):
    row = dict(
        account_id="demo",
        mode="demo",
        trade_date=str(DAY),
        event_time=at(DAY, 15, 0).isoformat(),
        exchange="XSHG",
        symbol="600000.SH",
        side="BUY",
        quantity="100",
        price="10",
        gross_amount="1000",
        commission="5",
        tax="0",
        other_fees="0",
        broker_order_id="o1",
        broker_trade_id="t1",
        sellable_on="2025-05-02",
    )
    return row | changes


def test_preview_does_not_mutate_atomic_apply_and_cross_file_dedupe(
    tmp_path, ledger, broker_mapping
):
    original = ledger.state()
    path = tmp_path / "fills.csv"
    write_csv(path, [fill_row()])
    batch = preview(ledger, tmp_path, broker_mapping, path, "fills")
    assert ledger.state() == original and batch["new_events"] == 1
    assert apply(ledger, tmp_path, batch["batch_id"])["new_events"] == 1
    assert apply(ledger, tmp_path, batch["batch_id"])["new_events"] == 0
    second = tmp_path / "different-export.csv"
    write_csv(second, [fill_row(), fill_row(broker_trade_id="t2", commission="0")])
    batch2 = preview(ledger, tmp_path, broker_mapping, second, "fills")
    assert batch2["new_events"] == 1
    apply(ledger, tmp_path, batch2["batch_id"])
    assert ledger.state()["cash"] == "7995.00" and ledger.state()["external_review"]
    assert ledger.view(at(DAY)).positions["600000.SH"].quantity == 200
    assert reconcile(ledger, DAY)["state"] == "PENDING"


@pytest.mark.parametrize(
    "changes",
    [
        {"broker_trade_id": ""},
        {"commission": ""},
        {"exchange": "XSHE"},
        {"quantity": "-100"},
        {"price": "NaN"},
        {"gross_amount": "999"},
        {"mode": "manual"},
        {"account_id": "manual"},
        {"event_time": "2025-04-30T15:00:00"},
        {"sellable_on": "2025-04-30"},
    ],
)
def test_import_rejects_ambiguous_incomplete_or_wrong_mode(
    tmp_path, ledger, broker_mapping, changes
):
    path = tmp_path / "invalid.csv"
    write_csv(path, [fill_row(**changes)])
    with pytest.raises(AshareError):
        preview(ledger, tmp_path, broker_mapping, path, "fills")
    assert len(ledger.events()) == 1 and ledger.state()["cash"] == "10000.00"


def test_batch_conflict_rolls_back_every_row(tmp_path, ledger, broker_mapping):
    path = tmp_path / "conflict.csv"
    write_csv(path, [fill_row(), fill_row(quantity="200", gross_amount="2000")])
    with pytest.raises(AshareError):
        preview(ledger, tmp_path, broker_mapping, path, "fills")
    assert len(ledger.events()) == 1


def test_apply_failure_after_new_row_is_atomic(tmp_path, ledger, broker_mapping, monkeypatch):
    path = tmp_path / "fills.csv"
    write_csv(path, [fill_row()])
    batch = preview(ledger, tmp_path, broker_mapping, path, "fills")
    import ashare.accounting.ledger as module

    original = module.reduce_event

    def fail(state, event):
        original(state, event)
        if event.kind == "Fill":
            raise RuntimeError("injected projection failure")

    monkeypatch.setattr(module, "reduce_event", fail)
    with pytest.raises(RuntimeError):
        apply(ledger, tmp_path, batch["batch_id"])
    assert len(ledger.events()) == 1
    assert (
        ledger.db.execute(
            "SELECT state FROM imports WHERE batch_id=?", (batch["batch_id"],)
        ).fetchone()[0]
        == "PREVIEWED"
    )
    monkeypatch.setattr(module, "reduce_event", original)
    assert apply(ledger, tmp_path, batch["batch_id"])["new_events"] == 1


def test_evidence_tampering_is_rejected(tmp_path, ledger, broker_mapping):
    path = tmp_path / "fills.csv"
    write_csv(path, [fill_row()])
    batch = preview(ledger, tmp_path, broker_mapping, path, "fills")
    source = tmp_path / "imports" / "demo" / batch["batch_id"] / "original.csv"
    source.write_text("tampered")
    with pytest.raises(AshareError, match="changed"):
        apply(ledger, tmp_path, batch["batch_id"])


def test_unknown_order_prevents_replacement_plan(market, config, ledger):
    root, snapshot, tables = market
    event = new_event(
        "demo",
        Mode.DEMO,
        "OrderRegistered",
        DAY,
        {
            "order_id": "unknown",
            "symbol": "510300.SH",
            "side": "BUY",
            "quantity": 100,
            "limit_price": "12",
            "fee_reserve": "5",
            "state": "UNKNOWN",
        },
        event_time=at(DAY, 14, 50),
    )
    ledger.append([event])
    with ledger.db:
        ledger.set_meta("reconciliation", "MATCHED")
    view = MarketView(snapshot, tables, at(DAY))
    target = ETFTrend(config).generate(view, DAY)
    result = plan(target, view, ledger.view(at(DAY)), config)
    assert result["state"] == "BLOCKED"
    assert any("UNSETTLED_ORDER" in reason for reason in result["blockers"])


def test_plan_identity_export_idempotence_and_external_change(market, config, ledger):
    root, snapshot, tables = market
    view = MarketView(snapshot, tables, at(DAY))
    target = ETFTrend(config).generate(view, DAY)
    first = plan(target, view, ledger.view(at(DAY)), config)
    second = plan(target, view, ledger.view(at(DAY)), config)
    assert first == second and first["intents"]
    plans.save(ledger, root, first)
    now = at(date(2025, 5, 2), 14, 50)
    assert (
        plans.preflight(ledger, config, first["plan_id"], snapshot, tables, now=now)["state"]
        == "RISK_PASSED"
    )
    exported = plans.export(ledger, root, first["plan_id"], now=now)
    assert exported["state"] == "EXPORTED" and not exported["broker_submission"]
    count = len(ledger.events())
    assert plans.export(ledger, root, first["plan_id"], now=now) == exported
    assert len(ledger.events()) == count
    ledger.append(
        [
            new_event(
                "demo",
                Mode.DEMO,
                "Deposit",
                date(2025, 5, 2),
                {"amount": "100"},
                key="newdeposit",
                event_time=at(date(2025, 5, 2), 14, 51),
            )
        ]
    )
    assert plans.get(ledger, first["plan_id"])["state"] == "SUPERSEDED"
    with pytest.raises(AshareError):
        plans.export(ledger, root, first["plan_id"], now=now)


def test_expired_plan_and_halt_block_export(market, config, ledger):
    root, snapshot, tables = market
    view = MarketView(snapshot, tables, at(DAY))
    target = ETFTrend(config).generate(view, DAY)
    draft = plans.save(ledger, root, plan(target, view, ledger.view(at(DAY)), config))
    result = plans.preflight(
        ledger, config, draft["plan_id"], snapshot, tables, now=at(date(2025, 5, 5), 14, 50)
    )
    assert "PLAN_WRONG_DAY" in result["reasons"]
    ledger.halt("operator halt")
    result = plans.preflight(
        ledger, config, draft["plan_id"], snapshot, tables, now=at(date(2025, 5, 2), 14, 50)
    )
    assert "ACCOUNT_OR_RISK_BLOCKED" in result["reasons"]
    with pytest.raises(AshareError):
        plans.export(ledger, root, draft["plan_id"], now=at(date(2025, 5, 2), 14, 50))


def test_order_report_cannot_manufacture_missing_fill(ledger):
    order = {
        "order_id": "o1",
        "symbol": "600000.SH",
        "side": "BUY",
        "quantity": 200,
        "limit_price": "10",
        "fee_reserve": "5",
        "state": "FILLED",
        "reported_filled": 200,
    }
    ledger.append([new_event("demo", Mode.DEMO, "OrderRegistered", DAY, order)])
    assert ledger.state()["orders"]["o1"]["state"] == "UNKNOWN"
    assert not ledger.view(at(DAY)).positions


def test_manual_rejects_synthetic_snapshot_even_with_explicit_approvals(market):
    from ashare.accounting.ledger import Ledger
    from ashare.config import Config, Fees, Risk

    root, snapshot, tables = market
    fees = Fees.model_validate(
        {**Fees().model_dump(mode="json"), "verified": True, "version": "confirmed-fixture-fees"}
    )
    risk = Risk.model_validate(
        {**Risk().model_dump(mode="json"), "approved": True, "version": "confirmed-fixture-risk"}
    )
    cfg = Config(
        mode=Mode.MANUAL,
        account_id="manual",
        fees=fees,
        risk=risk,
        broker_mapping_verified=True,
        broker_confirmed=True,
        strategy_approved=True,
    )
    ledger = Ledger(root / "state" / "manual.sqlite", "manual", Mode.MANUAL)
    try:
        ledger.append(
            [
                new_event(
                    "manual",
                    Mode.MANUAL,
                    "AccountInitialized",
                    DAY,
                    {"cash": "200000", "positions": []},
                    source="operator",
                    event_time=at(DAY, 8, 0),
                )
            ]
        )
        with ledger.db:
            ledger.set_meta("reconciliation", "MATCHED")
        view = MarketView(snapshot, tables, at(DAY))
        target = ETFTrend(cfg).generate(view, DAY)
        draft = plans.save(ledger, root, plan(target, view, ledger.view(at(DAY)), cfg))
        assert draft["state"] == "BLOCKED" and "SYNTHETIC_DATA_IN_MANUAL" in draft["blockers"]
        with pytest.raises(AshareError):
            plans.export(ledger, root, draft["plan_id"], now=at(date(2025, 5, 2), 14, 50))
        assert len(ledger.events()) == 1
    finally:
        ledger.close()
