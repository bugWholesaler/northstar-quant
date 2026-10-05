import json
from datetime import datetime

from ashare.domain.models import canonical, decimal, stable_id


def reconcile(ledger, day, operator=None):
    rows = ledger.db.execute(
        "SELECT batch_id,asof,body FROM broker_snapshots ORDER BY asof DESC"
    ).fetchall()
    matching = [r for r in rows if datetime.fromisoformat(r[1]).date() == day]
    differences = []
    if not matching:
        result = {
            "state": "PENDING",
            "day": str(day),
            "differences": [{"kind": "MISSING_ACCOUNT_SNAPSHOT"}],
        }
    else:
        bid, timestamp, body = matching[0]
        snapshot = json.loads(body)
        first = snapshot[0]
        view = ledger.view(datetime.fromisoformat(timestamp))
        if any(e.event_time > datetime.fromisoformat(timestamp) for e in ledger.events()):
            differences.append(
                {
                    "kind": "ASOF_MISMATCH",
                    "reason": "account contains facts later than broker snapshot",
                }
            )
        for key in ("cash_total", "cash_available", "cash_frozen", "receivables"):
            if decimal(first[key]) != getattr(view, key):
                differences.append(
                    {"kind": key, "broker": first[key], "ledger": str(getattr(view, key))}
                )
        holdings = {r["symbol"]: r for r in snapshot if r.get("symbol")}
        for symbol in sorted(set(holdings) | set(view.positions)):
            position = view.positions.get(symbol)
            for key in ("quantity", "sellable", "frozen"):
                broker = int(holdings.get(symbol, {}).get(key, 0))
                local = getattr(position, key) if position else 0
                if broker != local:
                    differences.append(
                        {"kind": key, "symbol": symbol, "broker": broker, "ledger": local}
                    )
        unknown = [
            o
            for o in ledger.state()["orders"].values()
            if o["state"] in ("UNKNOWN", "CANCEL_PENDING", "EXPORTED", "SUBMITTED")
        ]
        if unknown:
            differences.append(
                {"kind": "UNCONFIRMED_ORDERS", "orders": [o["order_id"] for o in unknown]}
            )
        for order in ledger.state()["orders"].values():
            if order.get("broker_order_id") and order.get("reported_filled") != order["filled"]:
                differences.append(
                    {
                        "kind": "ORDER_FILL_QUANTITY",
                        "order_id": order["order_id"],
                        "broker": order.get("reported_filled"),
                        "ledger": order["filled"],
                    }
                )
        # Imported order status alone does not prove cumulative fill quantities.
        result = {
            "state": "MISMATCH" if differences else "MATCHED",
            "day": str(day),
            "asof": timestamp,
            "snapshot_batch": bid,
            "differences": differences,
            "operator": operator,
        }
    with ledger.db:
        ledger.set_meta("reconciliation", result["state"])
        ledger.db.execute(
            "INSERT OR REPLACE INTO reconciliations VALUES(?,?,?,?)",
            (stable_id(result), str(day), result["state"], canonical(result)),
        )
        if ledger.state()["external_review"] and ledger.mode.value == "manual":
            ledger.set_meta("risk_state", "REVIEW")
    return result
