from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ashare.domain.errors import AshareError
from ashare.domain.models import (
    AccountView,
    ExecutionEvent,
    Mode,
    PositionView,
    at,
    canonical,
    decimal,
    money,
    stable_id,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_migrations VALUES(1);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL, event_time TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projection(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans(plan_id TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(batch_id TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS broker_snapshots(batch_id TEXT PRIMARY KEY,asof TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,day TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily_inputs(day TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reconciliations(id TEXT PRIMARY KEY,day TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL);
"""


def empty_state():
    return {
        "cash": "0",
        "receivables": {},
        "lots": [],
        "orders": {},
        "fee_totals": {},
        "written_down": [],
        "last_event": None,
        "external_review": False,
    }


def new_event(
    account: str,
    mode: Mode,
    kind: str,
    day: date,
    payload: dict,
    source="fixture",
    batch="fixture",
    key=None,
    event_time=None,
):
    timestamp = event_time or at(day, 15, 0)
    body = dict(
        account_id=account,
        mode=mode,
        kind=kind,
        event_time=timestamp,
        received_at=timestamp,
        source=source,
        source_batch=batch,
        payload=payload,
    )
    identity = {"account": account, "source": source, "kind": kind, "key": key or payload}
    return ExecutionEvent(event_id=stable_id(identity), **body)


def reduce_event(state: dict, event: ExecutionEvent):
    p, day = event.payload, event.event_time.date()
    cash = decimal(state["cash"])
    if event.kind in ("Deposit", "Withdrawal", "CashRecovery", "FeeAdjusted", "DividendTaxPaid"):
        amount = money(p["amount"])
        if amount < 0:
            raise AshareError("EVENT_AMOUNT", "negative event amount")
        cash += amount if event.kind in ("Deposit", "CashRecovery") else -amount
        if event.kind == "FeeAdjusted":
            state["fee_totals"][event.event_id] = str(amount)
    elif event.kind == "AccountInitialized":
        if state["lots"] or cash or state["last_event"]:
            raise AshareError("INITIALIZED", "account already initialized")
        cash = money(p["cash"])
        for item in p.get("positions", []):
            quantity = int(item["quantity"])
            if quantity < 0:
                raise AshareError("QUANTITY", "negative initial holding")
            state["lots"].append(
                {
                    "lot_id": stable_id(item),
                    "symbol": item["symbol"],
                    "quantity": quantity,
                    "acquired": item.get("acquired"),
                    "sellable_on": item["sellable_on"],
                    "cost": str(decimal(item.get("cost", 0))),
                    "tax_basis_known": bool(item.get("acquired")),
                }
            )
    elif event.kind == "OrderRegistered":
        oid = p["order_id"]
        allowed = {
            "INTENT",
            "EXPORTED",
            "SUBMITTED",
            "ACKNOWLEDGED",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCEL_PENDING",
            "CANCELLED",
            "REJECTED",
            "EXPIRED",
            "UNKNOWN",
        }
        if (
            p["state"] not in allowed
            or int(p["quantity"]) <= 0
            or p["side"] not in ("BUY", "SELL")
            or decimal(p["limit_price"]) <= 0
            or decimal(p.get("fee_reserve", 0)) < 0
        ):
            raise AshareError("ORDER_STATE", "invalid order")
        existing = state["orders"].get(oid)
        if existing:
            for field in ("symbol", "side", "quantity"):
                if existing[field] != p[field]:
                    raise AshareError("ORDER_CONFLICT", "order identity changed", field=field)
            transitions = {
                "INTENT": {"EXPORTED", "SUBMITTED", "UNKNOWN", "EXPIRED"},
                "EXPORTED": {"SUBMITTED", "UNKNOWN", "EXPIRED"},
                "SUBMITTED": {
                    "ACKNOWLEDGED",
                    "PARTIALLY_FILLED",
                    "FILLED",
                    "CANCEL_PENDING",
                    "CANCELLED",
                    "REJECTED",
                    "UNKNOWN",
                },
                "ACKNOWLEDGED": {
                    "PARTIALLY_FILLED",
                    "FILLED",
                    "CANCEL_PENDING",
                    "CANCELLED",
                    "UNKNOWN",
                },
                "PARTIALLY_FILLED": {"FILLED", "CANCEL_PENDING", "CANCELLED", "UNKNOWN"},
                "CANCEL_PENDING": {"CANCELLED", "PARTIALLY_FILLED", "FILLED", "UNKNOWN"},
                "UNKNOWN": allowed - {"INTENT", "EXPORTED"},
            }
            if (
                p["state"] != existing["state"]
                and not (
                    existing["filled"] == int(existing["quantity"])
                    and p["state"] in ("CANCELLED", "FILLED")
                )
                and p["state"] not in transitions.get(existing["state"], set())
            ):
                raise AshareError(
                    "ORDER_TRANSITION",
                    "illegal order transition",
                    previous=existing["state"],
                    next=p["state"],
                )
        filled = existing.get("filled", 0) if existing else 0
        effective_state = p["state"]
        if filled == int(p["quantity"]):
            effective_state = "FILLED"
        elif p["state"] == "FILLED":
            effective_state = "UNKNOWN"  # order report alone cannot manufacture missing fills
        state["orders"][oid] = {
            **p,
            "filled": filled,
            "reported_state": p["state"],
            "state": effective_state,
        }
    elif event.kind == "Fill":
        quantity, price = int(p["quantity"]), decimal(p["price"])
        fees = sum(money(p[k]) for k in ("commission", "tax", "other_fees"))
        if (
            quantity <= 0
            or price <= 0
            or fees < 0
            or any(decimal(p[k]) < 0 for k in ("commission", "tax", "other_fees"))
        ):
            raise AshareError("FILL_VALUE", "invalid fill")
        if money(price * quantity) != money(p["gross_amount"]):
            raise AshareError("FILL_AMOUNT", "fill gross amount mismatch")
        if p["side"] == "BUY":
            cash -= money(quantity * price) + fees
            if cash < 0:
                raise AshareError("INSUFFICIENT_CASH", "fill would make cash negative")
            state["lots"].append(
                {
                    "lot_id": event.event_id,
                    "symbol": p["symbol"],
                    "quantity": quantity,
                    "acquired": str(day),
                    "sellable_on": p["sellable_on"],
                    "cost": str(money(quantity * price) + fees),
                    "tax_basis_known": True,
                }
            )
        elif p["side"] == "SELL":
            remaining = quantity
            for lot in state["lots"]:
                if lot["symbol"] == p["symbol"] and date.fromisoformat(lot["sellable_on"]) <= day:
                    used = min(lot["quantity"], remaining)
                    if lot["quantity"]:
                        lot["cost"] = str(
                            decimal(lot["cost"]) * (lot["quantity"] - used) / lot["quantity"]
                        )
                    lot["quantity"] -= used
                    remaining -= used
            if remaining:
                raise AshareError(
                    "INSUFFICIENT_SELLABLE", "sell exceeds settled lots", remaining=remaining
                )
            cash += money(quantity * price) - fees
        else:
            raise AshareError("SIDE", "unknown fill side")
        oid = p.get("order_id")
        if oid in state["orders"]:
            order = state["orders"][oid]
            if order["symbol"] != p["symbol"] or order["side"] != p["side"]:
                raise AshareError("FILL_ORDER", "fill conflicts with order")
            order["filled"] += quantity
            if order["filled"] > int(order["quantity"]):
                raise AshareError("OVERFILL", "fill exceeds order")
            old_state = order["state"]
            order["state"] = (
                "FILLED"
                if order["filled"] == int(order["quantity"])
                else (
                    old_state
                    if old_state in ("CANCELLED", "CANCEL_PENDING")
                    else "PARTIALLY_FILLED"
                )
            )
        else:
            state["external_review"] = True
        if p.get("external", False):
            state["external_review"] = True
        state["fee_totals"][event.event_id] = str(fees)
    elif event.kind == "DividendEntitled":
        aid = p["action_id"]
        if aid in state["receivables"]:
            raise AshareError("DUPLICATE_ENTITLEMENT", "action already entitled")
        if decimal(p["amount"]) < 0:
            raise AshareError("EVENT_AMOUNT", "negative dividend entitlement")
        state["receivables"][aid] = str(money(p["amount"]))
    elif event.kind == "DividendPaid":
        amount = state["receivables"].pop(p["action_id"], None)
        if amount is None:
            raise AshareError("NO_ENTITLEMENT", "dividend payment without entitlement")
        cash += decimal(amount)
    elif event.kind == "SplitOrBonus":
        ratio = decimal(p["ratio"])
        if ratio < 0:
            raise AshareError("ACTION_RATIO", "negative bonus ratio")
        for lot in list(state["lots"]):
            if lot["symbol"] != p["symbol"] or not lot["quantity"]:
                continue
            bonus = decimal(lot["quantity"]) * ratio
            if bonus != int(bonus):
                raise AshareError("FRACTIONAL_ACTION", "fractional share policy not implemented")
            cost = decimal(lot["cost"]) / (1 + ratio)
            lot["cost"] = str(cost)
            state["lots"].append(
                {
                    **lot,
                    "lot_id": stable_id([event.event_id, lot["lot_id"]]),
                    "quantity": int(bonus),
                    "cost": str(cost * ratio),
                    "sellable_on": p["unlock_date"],
                }
            )
    elif event.kind == "SharesUnlocked":
        for lot in state["lots"]:
            if lot["lot_id"] in p["lot_ids"]:
                lot["sellable_on"] = str(day)
    elif event.kind == "AssetWrittenDown":
        state["written_down"].append(p["symbol"])
    elif event.kind == "Correction":
        raise AshareError(
            "CORRECTION_REQUIRES_REVIEW",
            "use evidenced cash/fee/action adjustments; arbitrary historical reversal is unsupported",
        )
    if cash < 0:
        raise AshareError("NEGATIVE_CASH", "event creates negative cash")
    state["cash"], state["last_event"] = str(money(cash)), event.event_id


class Ledger:
    def __init__(self, path: Path, account_id: str, mode: Mode):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.account_id, self.mode = path, account_id, mode
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        with self.db:
            for key, value in {
                "account_id": account_id,
                "mode": mode.value,
                "risk_state": "RUNNING",
                "reconciliation": "PENDING",
            }.items():
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES(?,?)", (key, value))
            if self.meta("mode") != mode.value or self.meta("account_id") != account_id:
                raise AshareError("MODE_ISOLATION", "database belongs to another mode/account")
            self.db.execute(
                "INSERT OR IGNORE INTO projection VALUES(1,?)", (canonical(empty_state()),)
            )

    def close(self):
        self.db.close()

    def meta(self, key):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, value))

    def state(self):
        return json.loads(self.db.execute("SELECT body FROM projection WHERE id=1").fetchone()[0])

    def events(self):
        return [
            ExecutionEvent.model_validate_json(row[0])
            for row in self.db.execute("SELECT body FROM events ORDER BY event_time,sequence")
        ]

    def append(self, events: list[ExecutionEvent], inside_transaction=False):
        if inside_transaction:
            return self._append(events)
        with self.db:
            return self._append(events)

    def _append(self, events):
        added = 0
        for event in events:
            if self.mode == Mode.MANUAL and event.source in (
                "rqalpha",
                "engine",
                "synthetic",
                "fixture",
            ):
                raise AshareError("MODE_ISOLATION", "simulated source cannot enter manual ledger")
            if event.account_id != self.account_id or event.mode != self.mode:
                raise AshareError("MODE_ISOLATION", "event account/mode mismatch")
            existing = self.db.execute(
                "SELECT body FROM events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if existing:
                previous = ExecutionEvent.model_validate_json(existing[0])
                if (
                    previous.kind != event.kind
                    or {
                        k: v
                        for k, v in previous.payload.items()
                        if previous.kind != "Fill" or k not in ("external", "order_id")
                    }
                    != {
                        k: v
                        for k, v in event.payload.items()
                        if event.kind != "Fill" or k not in ("external", "order_id")
                    }
                    or previous.event_time != event.event_time
                    or previous.source != event.source
                ):
                    raise AshareError(
                        "EVENT_CONFLICT",
                        "same business id has different facts",
                        event_id=event.event_id,
                    )
                continue
            self.db.execute(
                "INSERT INTO events(event_id,event_time,body) VALUES(?,?,?)",
                (event.event_id, event.event_time.isoformat(), canonical(event)),
            )
            added += 1
        if added:
            state = empty_state()
            for event in self.events():
                reduce_event(state, event)
            self.db.execute("UPDATE projection SET body=? WHERE id=1", (canonical(state),))
            self.set_meta("reconciliation", "PENDING")
            self.db.execute(
                "UPDATE plans SET state='SUPERSEDED' WHERE state IN ('DRAFT','RISK_PASSED','EXPORTED')"
            )
        return added

    def replay(self):
        state = empty_state()
        for event in self.events():
            reduce_event(state, event)
        if state != self.state():
            raise AshareError("REPLAY_MISMATCH", "event replay differs from projection")
        return state

    def view(self, timestamp: datetime):
        visible_events = [event for event in self.events() if event.event_time <= timestamp]
        if len(visible_events) == len(self.events()):
            state = self.state()
        else:
            state = empty_state()
            for event in visible_events:
                reduce_event(state, event)
        positions, frozen_cash = {}, Decimal(0)
        for lot in state["lots"]:
            if lot["quantity"]:
                p = positions.setdefault(lot["symbol"], {"quantity": 0, "sellable": 0, "frozen": 0})
                p["quantity"] += lot["quantity"]
                if date.fromisoformat(lot["sellable_on"]) <= timestamp.date():
                    p["sellable"] += lot["quantity"]
        for order in state["orders"].values():
            if order["state"] in (
                "INTENT",
                "EXPORTED",
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
                "CANCEL_PENDING",
                "UNKNOWN",
            ):
                remaining = int(order["quantity"]) - int(order["filled"])
                if order["side"] == "BUY":
                    frozen_cash += remaining * decimal(order["limit_price"]) + decimal(
                        order.get("fee_reserve", 0)
                    )
                elif order["symbol"] in positions:
                    positions[order["symbol"]]["frozen"] += remaining
        for position in positions.values():
            position["sellable"] = max(0, position["sellable"] - position["frozen"])
        return AccountView(
            account_id=self.account_id,
            mode=self.mode,
            asof=timestamp,
            cash_total=decimal(state["cash"]),
            cash_available=max(Decimal(0), decimal(state["cash"]) - frozen_cash),
            cash_frozen=frozen_cash,
            receivables=sum(decimal(v) for v in state["receivables"].values()),
            positions={k: PositionView(**v) for k, v in positions.items()},
            reconciliation=self.meta("reconciliation"),
            risk_state=self.meta("risk_state"),
            event_hash=stable_id([e.model_dump(mode="json") for e in visible_events]),
            pending_orders=[
                o
                for o in state["orders"].values()
                if o["state"] not in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")
            ],
        )

    def halt(self, reason):
        with self.db:
            self.set_meta("risk_state", "HALTED")
            self.set_meta("halt_reason", reason)

    def resume(self, operator, reason):
        if self.meta("reconciliation") != "MATCHED" or not operator or not reason:
            raise AshareError(
                "RESUME_BLOCKED", "matched reconciliation, operator and reason required"
            )
        if any(o["state"] == "UNKNOWN" for o in self.state()["orders"].values()):
            raise AshareError("RESUME_BLOCKED", "unknown orders remain")
        with self.db:
            self.db.execute(
                "UPDATE plans SET state='SUPERSEDED' WHERE state IN ('DRAFT','EXPORTED','RISK_PASSED')"
            )
            self.set_meta("risk_state", "RUNNING")
            self.set_meta(
                "resume_record",
                canonical(
                    {
                        "operator": operator,
                        "reason": reason,
                        "at": datetime.now().astimezone().isoformat(),
                    }
                ),
            )
