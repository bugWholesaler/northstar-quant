from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ashare.accounting.ledger import Ledger, new_event
from ashare.config import Config
from ashare.domain.errors import AshareError, Unsupported
from ashare.domain.models import Instrument, Mode, TargetPortfolio, at
from ashare.ports.interfaces import Registry


def instrument(**changes):
    values = dict(
        instrument_id="600000.SH",
        symbol="600000.SH",
        exchange="XSHG",
        asset_type="stock",
        name="fixture",
        board="MAIN",
        listed_on=date(2024, 1, 1),
        tick=Decimal("0.01"),
    )
    return Instrument(**(values | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "2.0"},
        {"exchange": "XSHE"},
        {"tick": "NaN"},
        {"symbol": "600000"},
        {"instrument_id": "688000.SH", "symbol": "688000.SH"},
        {"lot": -1},
        {"tick": "0.001"},
        {"currency": "USD"},
    ],
)
def test_instrument_rejects_invalid_contracts(changes):
    with pytest.raises(ValidationError):
        instrument(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"weights": {"600000.SH": Decimal("NaN")}},
        {"weights": {"600000.SH": Decimal(-1)}},
        {"weights": {"a": Decimal("0.8"), "b": Decimal("0.8")}},
        {"decision_at": datetime(2025, 4, 30, 21, 30)},
        {"trade_not_before": date(2025, 4, 30)},
    ],
)
def test_target_rejects_invalid_contracts(changes):
    args = dict(
        target_id="fixture",
        strategy_id="fixture",
        decision_at=at(date(2025, 4, 30)),
        trade_not_before=date(2025, 5, 2),
        expires_at=date(2025, 5, 6),
        snapshot_id="fixture",
        weights={"600000.SH": Decimal("0.1")},
        reasons={},
    )
    with pytest.raises(ValidationError):
        TargetPortfolio(**(args | changes))


def test_registry_requires_explicit_registration():
    registry = Registry()
    with pytest.raises(Unsupported):
        registry.resolve("automatic_live_broker")
    registry.register("csv", object)
    assert registry.resolve("csv") is object
    with pytest.raises(ValueError):
        registry.register("csv", object)


def test_manual_does_not_inherit_permission():
    manual = Config(mode=Mode.MANUAL, account_id="manual")
    assert len(manual.manual_blockers()) == 5
    with pytest.raises(ValidationError):
        Config(mode=Mode.PAPER, account_id="manual", availability="observed")
    with pytest.raises(ValidationError):
        Config(mode=Mode.PAPER, account_id="paper")


def test_physical_and_event_mode_isolation(tmp_path, ledger):
    with pytest.raises(AshareError, match="database belongs"):
        Ledger(ledger.path, "manual", Mode.MANUAL)
    event = new_event("demo", Mode.RESEARCH, "Deposit", date(2025, 5, 2), {"amount": "100"})
    with pytest.raises(AshareError, match="mode mismatch"):
        ledger.append([event])
    assert ledger.state()["cash"] == "10000.00"


def test_manual_flags_cannot_approve_missing_or_example_budget():
    from ashare.config import Fees, Risk

    cfg = Config(
        mode=Mode.MANUAL,
        account_id="manual",
        fees=Fees(verified=True),
        risk=Risk(approved=True),
        broker_mapping_verified=True,
        broker_confirmed=True,
        strategy_approved=True,
    )
    assert set(cfg.manual_blockers()) == {"actual_fee_schedule", "personal_risk_budget"}
    fees = Fees.model_validate(
        {**Fees().model_dump(mode="json"), "verified": True, "version": "confirmed-broker-test"}
    )
    risk = Risk.model_validate(
        {**Risk().model_dump(mode="json"), "approved": True, "version": "confirmed-personal-test"}
    )
    assert cfg.model_copy(update={"fees": fees, "risk": risk}).manual_blockers() == []
