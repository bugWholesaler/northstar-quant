from datetime import date
from decimal import Decimal

import pytest

from ashare.accounting.fees import dividend_tax_rate, estimate_fees
from ashare.accounting.ledger import Ledger, new_event
from ashare.config import Fees
from ashare.domain.errors import AshareError
from ashare.domain.models import Mode, at

D = date(2025, 4, 30)
NEXT = date(2025, 5, 2)


def fill(side, quantity, price, fees="5", tax="0", key="fixture", day=D, sellable=NEXT, **extra):
    payload = dict(
        symbol="600000.SH",
        side=side,
        quantity=quantity,
        price=str(price),
        gross_amount=str(Decimal(str(price)) * quantity),
        commission=fees,
        tax=tax,
        other_fees="0",
        sellable_on=str(sellable),
        **extra,
    )
    return new_event("demo", Mode.DEMO, "Fill", day, payload, key=key)


def test_hand_calculated_cash_t1_and_nav(ledger):
    buy = fill("BUY", 200, 10)
    ledger.append([buy])
    assert ledger.view(at(D)).cash_total == Decimal("7995")
    assert ledger.view(at(D)).positions["600000.SH"].sellable == 0
    with pytest.raises(AshareError):
        ledger.append([fill("SELL", 100, 11, key="same-day")])
    assert len(ledger.events()) == 2
    ledger.append([fill("SELL", 100, 11, "5", "0.55", key="sell", day=NEXT)])
    view = ledger.view(at(NEXT))
    assert view.cash_total == Decimal("9089.45")
    assert view.positions["600000.SH"].quantity == 100
    assert view.cash_total + view.positions["600000.SH"].quantity * 11 == Decimal("10189.45")
    assert ledger.append([buy]) == 0
    assert ledger.replay() == ledger.state()


def test_receivable_payment_does_not_invent_return(ledger):
    ledger.append([fill("BUY", 100, 10, "0")])
    entitlement = new_event(
        "demo",
        Mode.DEMO,
        "DividendEntitled",
        NEXT,
        {"action_id": "div1", "amount": "20"},
        key="div1",
    )
    ledger.append([entitlement])
    view = ledger.view(at(NEXT))
    assert view.cash_total + view.receivables + 100 * Decimal("9.8") == Decimal("10000")
    paid = new_event(
        "demo", Mode.DEMO, "DividendPaid", date(2025, 5, 5), {"action_id": "div1"}, key="div1-pay"
    )
    ledger.append([paid])
    assert ledger.view(at(date(2025, 5, 5))).receivables == 0
    assert ledger.view(at(date(2025, 5, 5))).cash_total == Decimal("9020")
    assert ledger.append([paid]) == 0


def test_bonus_lots_preserve_cost_and_unlock_date(ledger):
    ledger.append([fill("BUY", 200, 10)])
    ledger.append(
        [
            new_event(
                "demo",
                Mode.DEMO,
                "SplitOrBonus",
                NEXT,
                {"symbol": "600000.SH", "ratio": "0.5", "unlock_date": "2025-05-06"},
                key="bonus",
            )
        ]
    )
    assert ledger.view(at(NEXT)).positions["600000.SH"].quantity == 300
    assert ledger.view(at(NEXT)).positions["600000.SH"].sellable == 200
    assert ledger.view(at(date(2025, 5, 6))).positions["600000.SH"].sellable == 300
    assert sum(Decimal(lot["cost"]) for lot in ledger.state()["lots"]) == Decimal("2005")


def test_partial_fills_late_fee_distinct_equal_trades(ledger):
    order = {
        "order_id": "o1",
        "symbol": "600000.SH",
        "side": "BUY",
        "quantity": 300,
        "limit_price": "10",
        "fee_reserve": "5",
        "state": "ACKNOWLEDGED",
    }
    ledger.append(
        [new_event("demo", Mode.DEMO, "OrderRegistered", D, order, event_time=at(D, 14, 50))]
    )
    for n in range(3):
        ledger.append([fill("BUY", 100, 10, "5" if n == 0 else "0", key=f"t{n}", order_id="o1")])
    assert ledger.state()["cash"] == "6995.00"
    assert ledger.state()["orders"]["o1"]["state"] == "FILLED"
    assert len([e for e in ledger.events() if e.kind == "Fill"]) == 3
    ledger.append(
        [
            new_event(
                "demo",
                Mode.DEMO,
                "FeeAdjusted",
                NEXT,
                {"amount": "2.30", "reference": "o1"},
                key="late-fee",
            )
        ]
    )
    assert ledger.state()["cash"] == "6992.70"


def test_cancel_pending_and_late_fill(ledger):
    order = {
        "order_id": "o1",
        "symbol": "600000.SH",
        "side": "BUY",
        "quantity": 200,
        "limit_price": "10",
        "fee_reserve": "5",
        "state": "ACKNOWLEDGED",
    }
    ledger.append(
        [
            new_event(
                "demo", Mode.DEMO, "OrderRegistered", D, order, key="ack", event_time=at(D, 14, 50)
            )
        ]
    )
    ledger.append(
        [
            new_event(
                "demo",
                Mode.DEMO,
                "OrderRegistered",
                D,
                {**order, "state": "CANCEL_PENDING"},
                key="cancel",
                event_time=at(D, 14, 51),
            )
        ]
    )
    ledger.append([fill("BUY", 100, 10, key="partial", order_id="o1")])
    ledger.append(
        [
            new_event(
                "demo",
                Mode.DEMO,
                "OrderRegistered",
                D,
                {**order, "state": "CANCELLED"},
                key="cancelled",
                event_time=at(D, 16, 0),
            )
        ]
    )
    assert ledger.view(at(D)).cash_frozen == 0
    # A report received later still carries its actual earlier execution time.
    ledger.append([fill("BUY", 100, 10, "0", key="late", order_id="o1")])
    assert ledger.state()["orders"]["o1"]["filled"] == 200
    assert ledger.view(at(D)).positions["600000.SH"].quantity == 200


def test_duplicate_id_conflict_rolls_back(ledger):
    original = fill("BUY", 100, 10, key="id")
    ledger.append([original])
    with pytest.raises(AshareError, match="different facts"):
        ledger.append([fill("BUY", 200, 10, key="id")])
    assert ledger.view(at(D)).positions["600000.SH"].quantity == 100


@pytest.mark.parametrize(
    "acquired,sold,asset,expected",
    [
        (date(2025, 4, 1), date(2025, 4, 30), "stock", "0.2"),
        (date(2025, 3, 1), date(2025, 4, 30), "stock", "0.1"),
        (date(2024, 4, 1), date(2025, 4, 30), "stock", "0"),
        (date(2025, 4, 1), date(2025, 4, 30), "etf", "0"),
        (None, date(2025, 4, 30), "stock", None),
    ],
)
def test_calendar_period_dividend_tax(acquired, sold, asset, expected):
    value = dividend_tax_rate(acquired, sold, asset)
    assert value == (Decimal(expected) if expected is not None else None)


def test_date_and_asset_specific_fees():
    before = estimate_fees(Fees(), "stock", "SELL", Decimal(1000), date(2023, 8, 25))
    after = estimate_fees(Fees(), "stock", "SELL", Decimal(1000), date(2023, 8, 28))
    etf = estimate_fees(Fees(), "etf", "SELL", Decimal(1000), date(2023, 8, 28))
    assert before["tax"] == Decimal(1) and after["tax"] == Decimal("0.5") and etf["tax"] == 0


def test_halt_persists_and_resume_requires_reconciliation(ledger):
    ledger.halt("test")
    reopened = Ledger(ledger.path, "demo", Mode.DEMO)
    try:
        assert reopened.meta("risk_state") == "HALTED"
        with reopened.db:
            reopened.set_meta("reconciliation", "MISMATCH")
        with pytest.raises(AshareError):
            reopened.resume("operator", "fixed")
        with reopened.db:
            reopened.set_meta("reconciliation", "MATCHED")
        reopened.resume("operator", "fixed with evidence")
        assert reopened.meta("risk_state") == "RUNNING"
    finally:
        reopened.close()


def test_historical_account_view_excludes_later_economic_facts(ledger):
    ledger.append([fill("BUY", 200, 10)])
    before = ledger.view(at(D))
    ledger.append([new_event("demo", Mode.DEMO, "Deposit", NEXT, {"amount": "10000"}, key="later")])
    assert ledger.view(at(D)) == before
    assert ledger.view(at(NEXT)).cash_total == Decimal("17995")
