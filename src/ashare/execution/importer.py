from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime
from pathlib import Path

import yaml

from ashare.accounting.ledger import new_event
from ashare.data.snapshot import atomic_json, file_hash
from ashare.domain.errors import AshareError
from ashare.domain.models import SH, ExecutionEvent, at, canonical, decimal, money, stable_id

KINDS = {"orders", "fills", "cash_movements", "account_snapshot"}


def valid_symbol(row):
    symbol = row.get("symbol", "")
    if not re.fullmatch(r"\d{6}\.(SH|SZ)", symbol):
        raise AshareError("SYMBOL", "canonical symbol required")
    if row["exchange"] != ("XSHG" if symbol.endswith("SH") else "XSHE"):
        raise AshareError("SYMBOL", "symbol/exchange mismatch")


def scoped_order(ledger, row):
    account = ledger.account_id
    candidates = [
        o
        for o in ledger.state()["orders"].values()
        if o.get("broker_order_id") == row.get("broker_order_id")
        and o["symbol"] == row.get("symbol")
        and o.get("broker_order_date", row["trade_date"]) == row["trade_date"]
    ]
    if len(candidates) == 1:
        return candidates[0]["order_id"]
    if len(candidates) > 1:
        raise AshareError("AMBIGUOUS_ORDER", "broker id matches multiple orders")
    if not row.get("broker_order_id"):
        raise AshareError("ORDER_ID_REQUIRED", "stable broker order id required")
    return stable_id([account, row["trade_date"], row["exchange"], row["broker_order_id"]])


def preview(ledger, root: Path, mapping_path: Path, file: Path, kind: str | None = None):
    mapping = yaml.safe_load(mapping_path.read_text())
    if (
        mapping.get("schema_version") != "1.0"
        or mapping.get("business_id_scope") != "account_date_exchange"
    ):
        raise AshareError("MAPPING_SCHEMA", "mapping must declare v1 and broker id scope")
    kind = kind or mapping.get("kind")
    if kind not in KINDS:
        raise AshareError("IMPORT_KIND", "one of four supported CSV kinds required")
    raw = file.read_bytes()
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("gb18030")
    rows, events, snapshots = [], [], []
    bid = stable_id(
        {
            "account": ledger.account_id,
            "mode": ledger.mode.value,
            "file": file_hash(file),
            "mapping": mapping,
            "kind": kind,
        }
    )
    directory = root / "imports" / ledger.account_id / bid
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "original.csv").write_bytes(raw)
    atomic_json(directory / "mapping.json", mapping)
    for index, source_row in enumerate(csv.DictReader(io.StringIO(decoded)), 2):
        row = {mapping.get("columns", {}).get(k, k): v.strip() for k, v in source_row.items()}
        row["_source_line"] = index
        try:
            if row.get("account_id") != ledger.account_id:
                raise AshareError("MODE_ISOLATION", "CSV account does not match selected account")
            if row.get("mode") != ledger.mode.value:
                raise AshareError("MODE_ISOLATION", "CSV mode must explicitly match selected mode")
            day = date.fromisoformat(row.get("trade_date") or row["asof"][:10])
            when = (
                datetime.fromisoformat(row["event_time"])
                if row.get("event_time")
                else at(day, 15, 0)
            )
            if when.tzinfo is None or when.astimezone(SH).date() != day:
                raise AshareError("TIMESTAMP", "market timestamp/date mismatch or timezone missing")
            payload = None
            if kind == "fills":
                valid_symbol(row)
                if not row.get("broker_trade_id"):
                    raise AshareError(
                        "AMBIGUOUS_FILL",
                        "stable broker trade id required; never deduplicate by price/quantity",
                    )
                for key in ("commission", "tax", "other_fees"):
                    if row.get(key, "") == "":
                        raise AshareError(
                            "FEE_PENDING", "unknown fees require settlement confirmation", field=key
                        )
                quantity, price = int(row["quantity"]), decimal(row["price"])
                if quantity <= 0 or price <= 0 or row["side"] not in ("BUY", "SELL"):
                    raise AshareError("FILL_VALUE", "invalid quantity/price/side")
                if money(quantity * price) != money(row["gross_amount"]):
                    raise AshareError(
                        "FILL_AMOUNT", "gross amount differs from price times quantity"
                    )
                if row["side"] == "BUY":
                    date.fromisoformat(row["sellable_on"])
                    if row["sellable_on"] <= str(day):
                        raise AshareError("T_PLUS_ONE", "buy requires future sellable date")
                payload = {
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "quantity": quantity,
                    "price": str(price),
                    "gross_amount": str(money(row["gross_amount"])),
                    "commission": str(money(row["commission"])),
                    "tax": str(money(row["tax"])),
                    "other_fees": str(money(row["other_fees"])),
                    "order_id": scoped_order(ledger, row),
                    "sellable_on": row.get("sellable_on", str(day)),
                    "external": not bool(
                        ledger.state()["orders"].get(scoped_order(ledger, row), {}).get("intent_id")
                    ),
                }
                key = [ledger.account_id, day, row["exchange"], row["broker_trade_id"]]
                events.append(
                    new_event(
                        ledger.account_id,
                        ledger.mode,
                        "Fill",
                        day,
                        payload,
                        mapping["broker"],
                        bid,
                        key,
                        when,
                    )
                )
            elif kind == "orders":
                valid_symbol(row)
                payload = {
                    "order_id": scoped_order(ledger, row),
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "quantity": int(row["quantity"]),
                    "limit_price": str(decimal(row["limit_price"])),
                    "fee_reserve": str(money(row["fee_reserve"])),
                    "state": row["state"],
                    "broker_order_id": row["broker_order_id"],
                    "broker_order_date": row["trade_date"],
                    "reported_filled": int(row["filled_quantity"]),
                    "intent_id": row.get("intent_id"),
                }
                events.append(
                    new_event(
                        ledger.account_id,
                        ledger.mode,
                        "OrderRegistered",
                        day,
                        payload,
                        mapping["broker"],
                        bid,
                        [payload["order_id"], row["state"], when],
                        when,
                    )
                )
            elif kind == "cash_movements":
                if row["kind"] not in (
                    "Deposit",
                    "Withdrawal",
                    "FeeAdjusted",
                    "DividendTaxPaid",
                    "CashRecovery",
                ):
                    raise AshareError("CASH_KIND", "unsupported cash movement")
                if not row.get("movement_id") or decimal(row["amount"]) < 0:
                    raise AshareError("CASH_VALUE", "movement id and nonnegative amount required")
                payload = {
                    "amount": str(money(row["amount"])),
                    "reference": row.get("reference", ""),
                }
                events.append(
                    new_event(
                        ledger.account_id,
                        ledger.mode,
                        row["kind"],
                        day,
                        payload,
                        mapping["broker"],
                        bid,
                        [ledger.account_id, day, row["movement_id"]],
                        when,
                    )
                )
            elif kind == "account_snapshot":
                timestamp = datetime.fromisoformat(row["asof"])
                if timestamp.tzinfo is None:
                    raise AshareError("TIMESTAMP", "snapshot timestamp requires timezone")
                if row.get("symbol"):
                    valid_symbol(row)
                    for key in ("quantity", "sellable", "frozen"):
                        if int(row[key]) < 0:
                            raise AshareError("QUANTITY", "negative snapshot quantity")
                for key in ("cash_total", "cash_available", "cash_frozen", "receivables"):
                    decimal(row[key])
                snapshots.append(row)
            rows.append(row)
        except (ValueError, KeyError, AshareError) as error:
            atomic_json(
                directory / "validation.json",
                {
                    "batch_id": bid,
                    "state": "REJECTED",
                    "line": index,
                    "error": getattr(error, "code", "VALUE"),
                    "message": str(error),
                },
            )
            raise AshareError(
                "IMPORT_ROW",
                str(error),
                file=file.name,
                line=index,
                cause=getattr(error, "code", "VALUE"),
                batch_id=bid,
            ) from error
    if not rows:
        raise AshareError("EMPTY_IMPORT", "empty CSV does not prove no fills/orders")
    if snapshots:
        balances = [
            (
                r["asof"],
                *(r[k] for k in ("cash_total", "cash_available", "cash_frozen", "receivables")),
            )
            for r in snapshots
        ]
        if len(set(balances)) != 1 or len(
            [r["symbol"] for r in snapshots if r.get("symbol")]
        ) != len(set(r["symbol"] for r in snapshots if r.get("symbol"))):
            raise AshareError("SNAPSHOT_CONFLICT", "inconsistent cash/time or duplicate holdings")
    # Preview uses a rolled-back transaction and the same projection reducer as apply.
    ledger.db.execute("SAVEPOINT preview")
    try:
        added = ledger.append(events, inside_transaction=True)
        projected = ledger.view(at(day)).model_dump(mode="json")
    finally:
        ledger.db.execute("ROLLBACK TO preview")
        ledger.db.execute("RELEASE preview")
    directory = root / "imports" / ledger.account_id / bid
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "original.csv").write_bytes(raw)
    atomic_json(directory / "mapping.json", mapping)
    batch = {
        "schema_version": "1.0",
        "batch_id": bid,
        "account_id": ledger.account_id,
        "mode": ledger.mode.value,
        "kind": kind,
        "source_hash": file_hash(directory / "original.csv"),
        "mapping_hash": file_hash(directory / "mapping.json"),
        "rows": rows,
        "events": [e.model_dump(mode="json") for e in events],
        "snapshots": snapshots,
        "new_events": added,
        "projected": projected,
        "original_account_hash": ledger.view(at(day)).event_hash,
    }
    atomic_json(directory / "validation.json", batch)
    with ledger.db:
        previous = ledger.db.execute(
            "SELECT state FROM imports WHERE batch_id=?", (bid,)
        ).fetchone()
        if not previous:
            ledger.db.execute(
                "INSERT INTO imports VALUES(?,?,?)", (bid, canonical(batch), "PREVIEWED")
            )
    return batch


def apply(ledger, root: Path, bid: str):
    row = ledger.db.execute("SELECT body,state FROM imports WHERE batch_id=?", (bid,)).fetchone()
    if row is None:
        raise AshareError("IMPORT_NOT_PREVIEWED", "batch must be previewed first")
    batch = json.loads(row[0])
    directory = root / "imports" / ledger.account_id / bid
    if (
        file_hash(directory / "original.csv") != batch["source_hash"]
        or file_hash(directory / "mapping.json") != batch["mapping_hash"]
    ):
        raise AshareError("IMPORT_HASH", "source evidence changed after preview")
    if row[1] == "APPLIED":
        return {"batch_id": bid, "new_events": 0, "state": "APPLIED"}
    events = [ExecutionEvent.model_validate(e) for e in batch["events"]]
    with ledger.db:
        # Recompute against current state rather than applying an obsolete preview projection.
        added = ledger.append(events, inside_transaction=True)
        if batch["snapshots"]:
            timestamp = batch["snapshots"][0]["asof"]
            ledger.db.execute(
                "INSERT OR REPLACE INTO broker_snapshots VALUES(?,?,?)",
                (bid, timestamp, canonical(batch["snapshots"])),
            )
            ledger.set_meta("reconciliation", "PENDING")
        ledger.db.execute("UPDATE imports SET state='APPLIED' WHERE batch_id=?", (bid,))
    return {"batch_id": bid, "new_events": added, "state": "APPLIED"}
