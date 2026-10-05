import csv
import html
import json
from datetime import datetime
from pathlib import Path

from ashare.accounting.ledger import new_event
from ashare.data.snapshot import atomic_json
from ashare.domain.errors import AshareError
from ashare.domain.models import SH, OrderIntent, canonical, decimal, stable_id
from ashare.portfolio.planner import ensure_price_quantity


def save(ledger, root, plan, inside_transaction=False):
    def mutate():
        ledger.db.execute(
            "UPDATE plans SET state='SUPERSEDED' WHERE plan_id<>? AND state IN ('DRAFT','RISK_PASSED','EXPORTED')",
            (plan["plan_id"],),
        )
        ledger.db.execute(
            "INSERT OR IGNORE INTO plans VALUES(?,?,?)",
            (plan["plan_id"], canonical(plan), plan["state"]),
        )

    if inside_transaction:
        mutate()
    else:
        with ledger.db:
            mutate()
    atomic_json(root / "plans" / ledger.account_id / plan["plan_id"] / "plan.json", plan)
    return plan


def get(ledger, pid):
    row = ledger.db.execute("SELECT body,state FROM plans WHERE plan_id=?", (pid,)).fetchone()
    if not row:
        raise AshareError("PLAN_NOT_FOUND", "unknown plan")
    return {**json.loads(row[0]), "state": row[1]}


def preflight(ledger, config, pid, snapshot, tables, confirmation=None, now=None):
    plan = get(ledger, pid)
    now = now or datetime.now(SH)
    reasons = list(plan["blockers"])
    ledger.replay()
    view = ledger.view(now)
    if (
        sum(
            decimal(i["limit_price"]) * i["quantity"] + decimal(i["estimated_fees"])
            for i in plan["intents"]
            if i["side"] == "BUY"
        )
        > view.cash_available
    ):
        reasons.append("INSUFFICIENT_CASH")
    if plan["state"] in ("SUPERSEDED", "EXPIRED", "RECONCILED"):
        reasons.append("PLAN_" + plan["state"])
    if view.event_hash != plan["account_hash"]:
        reasons.append("ACCOUNT_CHANGED")
    if view.reconciliation != "MATCHED" or view.risk_state != "RUNNING":
        reasons.append("ACCOUNT_OR_RISK_BLOCKED")
    if stable_id(config) != plan["config_hash"]:
        reasons.append("CONFIG_CHANGED")
    if snapshot.snapshot_id != plan["snapshot_id"]:
        reasons.append("SNAPSHOT_CHANGED")
    if now.date().isoformat() != plan["valid_date"]:
        reasons.append("PLAN_WRONG_DAY")
    reasons.extend(config.manual_blockers())
    if ledger.mode != config.mode or ledger.account_id != config.account_id:
        reasons.append("MODE_ISOLATION")
    if ledger.mode.value == "manual" and snapshot.scope == "synthetic_sample":
        reasons.append("SYNTHETIC_DATA_IN_MANUAL")
    instruments = {i.instrument_id: i for i in tables["instruments"]}
    if config.mode.value == "manual":
        if (
            not confirmation
            or confirmation.get("account_hash") != view.event_hash
            or not confirmation.get("operator")
        ):
            reasons.append("LIVE_CONFIRMATION_REQUIRED")
        else:
            checked = datetime.fromisoformat(confirmation["checked_at"])
            if checked.tzinfo is None or not 0 <= (now - checked).total_seconds() <= 300:
                reasons.append("CONFIRMATION_STALE")
            if not ((14, 50) <= (now.hour, now.minute) <= (14, 56)):
                reasons.append("OUTSIDE_EXECUTION_WINDOW")
            if decimal(confirmation["cash_available"]) != view.cash_available:
                reasons.append("LIVE_CASH_MISMATCH")
    for row in plan["intents"]:
        intent = OrderIntent.model_validate(row)
        position = view.positions.get(intent.instrument_id)
        ensure_price_quantity(
            intent, instruments[intent.instrument_id], position.sellable if position else 0
        )
        if config.mode.value == "manual" and confirmation:
            quote = confirmation.get("quotes", {}).get(intent.instrument_id)
            if (
                not quote
                or not {
                    "price",
                    "limit_up",
                    "limit_down",
                    "suspended",
                    "st",
                    "delisting_period",
                    "sellable",
                }
                <= quote.keys()
                or quote.get("suspended")
                or (intent.side == "BUY" and (quote.get("st") or quote.get("delisting_period")))
            ):
                reasons.append("LIVE_STATUS:" + intent.instrument_id)
            elif intent.side == "SELL" and int(quote["sellable"]) != (
                position.sellable if position else 0
            ):
                reasons.append("LIVE_SELLABLE_MISMATCH:" + intent.instrument_id)
            elif (
                not decimal(quote["limit_down"]) <= intent.limit_price <= decimal(quote["limit_up"])
            ):
                reasons.append("LIVE_LIMIT:" + intent.instrument_id)
            elif (
                abs(decimal(quote["price"]) / intent.reference_price - 1)
                > config.risk.price_protection
            ):
                reasons.append("LIVE_PRICE_DEVIATION:" + intent.instrument_id)
        if any(
            o["symbol"] == intent.instrument_id
            and o["side"] == intent.side
            and o["state"]
            in (
                "UNKNOWN",
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
                "EXPORTED",
                "CANCEL_PENDING",
            )
            for o in ledger.state()["orders"].values()
        ):
            reasons.append("UNSETTLED_ORDER:" + intent.instrument_id)
    state = "BLOCKED" if reasons else "RISK_PASSED"
    with ledger.db:
        ledger.db.execute("UPDATE plans SET state=? WHERE plan_id=?", (state, pid))
        ledger.set_meta(
            "preflight:" + pid,
            canonical(
                {
                    "checked_at": now.isoformat(),
                    "reasons": reasons,
                    "confirmation": confirmation,
                    "account_hash": view.event_hash,
                }
            ),
        )
    return {"plan_id": pid, "state": state, "reasons": reasons}


def export(ledger, root: Path, pid: str, now=None):
    plan = get(ledger, pid)
    directory = root / "plans" / ledger.account_id / pid
    if plan["state"] == "EXPORTED":
        return {
            "plan_id": pid,
            "state": "EXPORTED",
            "directory": str(directory),
            "broker_submission": False,
        }
    if plan["state"] != "RISK_PASSED":
        raise AshareError("EXPORT_BLOCKED", "plan must pass preflight")
    now = now or datetime.now(SH)
    flight = json.loads(ledger.meta("preflight:" + pid))
    if (
        now.date().isoformat() != plan["valid_date"]
        or (now - datetime.fromisoformat(flight["checked_at"])).total_seconds() > 300
        or ledger.view(now).event_hash != flight["account_hash"]
    ):
        raise AshareError("PREFLIGHT_EXPIRED", "preflight expired or account changed")
    directory.mkdir(parents=True, exist_ok=True)
    headers = list(OrderIntent.model_fields)
    with (directory / "plan.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, headers)
        writer.writeheader()
        writer.writerows(plan["intents"])
    prior = [r[0] for r in ledger.db.execute("SELECT plan_id FROM plans WHERE state='SUPERSEDED'")]
    (directory / "plan.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Northstar 交易清单</title><h1>人工清单 · 导出不代表报单</h1><p>有效日期 '
        + html.escape(plan["valid_date"])
        + "</p><p>作废编号："
        + html.escape(", ".join(prior))
        + "</p><pre>"
        + html.escape(canonical(plan))
        + "</pre>"
    )
    events = []
    for row in plan["intents"]:
        i = OrderIntent.model_validate(row)
        events.append(
            new_event(
                ledger.account_id,
                ledger.mode,
                "OrderRegistered",
                now.date(),
                {
                    "order_id": i.intent_id,
                    "intent_id": i.intent_id,
                    "symbol": i.instrument_id,
                    "side": i.side,
                    "quantity": i.quantity,
                    "limit_price": str(i.limit_price),
                    "fee_reserve": str(i.estimated_fees),
                    "state": "EXPORTED",
                },
                "plan",
                pid,
                [i.intent_id, "export"],
                now,
            )
        )
    with ledger.db:
        ledger.append(events, inside_transaction=True)
        ledger.db.execute("UPDATE plans SET state='EXPORTED' WHERE plan_id=?", (pid,))
    atomic_json(directory / "plan.json", {**plan, "state": "EXPORTED", "superseded": prior})
    return {
        "plan_id": pid,
        "state": "EXPORTED",
        "directory": str(directory),
        "broker_submission": False,
    }


def register(ledger, intent_id, broker_order_id, state="SUBMITTED", now=None):
    now = now or datetime.now(SH)
    order = ledger.state()["orders"].get(intent_id)
    if order is None:
        raise AshareError("INTENT_NOT_EXPORTED", "intent must exist before manual registration")
    payload = {
        **order,
        "state": state,
        "broker_order_id": broker_order_id,
        "broker_order_date": str(now.date()),
    }
    event = new_event(
        ledger.account_id,
        ledger.mode,
        "OrderRegistered",
        now.date(),
        payload,
        "operator",
        key=[intent_id, state, broker_order_id],
        event_time=now,
    )
    ledger.append([event])
    return {
        "intent_id": intent_id,
        "state": state,
        "broker_order_id": broker_order_id,
        "broker_order_date": str(now.date()),
    }
