import csv
from datetime import date
from pathlib import Path

from ashare.accounting.ledger import Ledger, new_event
from ashare.adapters.data.csv_provider import CSVProvider
from ashare.config import Config
from ashare.data.snapshot import Store, atomic_json
from ashare.data.synthetic import generate
from ashare.domain.models import Mode, at
from ashare.execution.importer import apply, preview
from ashare.execution.reconcile import reconcile
from ashare.ops.backup import create, restore
from ashare.ops.daily import run as daily
from ashare.research.runner import run as research


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    generate(root / "synthetic-input")
    config = Config(mode=Mode.DEMO, account_id="demo", runtime=str(root), scope="synthetic_sample")
    ingestion = CSVProvider().ingest(
        root, root / "synthetic-input", "synthetic_sample", "historical_assumption"
    )
    sid = ingestion["snapshot_id"]
    run_result = research(root, config, sid, date(2025, 1, 30), date(2025, 5, 6))
    _, tables = Store(root).load(sid)
    ledger = Ledger(root / "state" / "demo.sqlite", "demo", Mode.DEMO)
    try:
        days = [
            c.trade_date
            for c in tables["calendar"]
            if c.exchange == "XSHG"
            and c.is_open
            and date(2025, 4, 24) <= c.trade_date <= date(2025, 5, 6)
        ]
        results = [daily(ledger, root, config, d, sid) for d in days]
        repeat = daily(ledger, root, config, days[-1], sid)
        assert repeat["repeated"]
        ledger.replay()
    finally:
        ledger.close()
    # Separate synthetic account exercise so imported external facts do not mutate
    # the frozen engine replay stream used by the sustained simulation above.
    imports_root = root / "import-exercise"
    mapping = Path(__file__).resolve().parents[2] / "configs" / "brokers" / "manual_csv.yaml"
    exercise = Ledger(imports_root / "state" / "demo.sqlite", "demo", Mode.DEMO)
    day, following = date(2025, 5, 2), date(2025, 5, 5)
    try:
        exercise.append(
            [
                new_event(
                    "demo", Mode.DEMO, "Deposit", day, {"amount": "10000"}, event_time=at(day, 8, 0)
                )
            ]
        )
        orders = [
            {
                "account_id": "demo",
                "mode": "demo",
                "trade_date": str(day),
                "event_time": at(day, 14, 50).isoformat(),
                "exchange": "XSHG",
                "symbol": "600000.SH",
                "side": "BUY",
                "quantity": "200",
                "limit_price": "10",
                "fee_reserve": "5",
                "broker_order_id": "demo-o1",
                "state": "ACKNOWLEDGED",
                "filled_quantity": "0",
                "intent_id": "",
            }
        ]
        write_csv(imports_root / "orders.csv", orders)
        batch = preview(exercise, imports_root, mapping, imports_root / "orders.csv", "orders")
        apply(exercise, imports_root, batch["batch_id"])
        fills = [
            {
                "account_id": "demo",
                "mode": "demo",
                "trade_date": str(day),
                "event_time": at(day, 14, 51 + n).isoformat(),
                "exchange": "XSHG",
                "symbol": "600000.SH",
                "side": "BUY",
                "quantity": "100",
                "price": "10",
                "gross_amount": "1000",
                "commission": "5" if n == 0 else "0",
                "tax": "0",
                "other_fees": "0",
                "broker_order_id": "demo-o1",
                "broker_trade_id": f"demo-t{n + 1}",
                "sellable_on": str(following),
            }
            for n in range(2)
        ]
        imported = []
        for n, row in enumerate(fills):
            path = imports_root / f"partial-{n}.csv"
            write_csv(path, [row])
            batch = preview(exercise, imports_root, mapping, path, "fills")
            imported.append(apply(exercise, imports_root, batch["batch_id"]))
        write_csv(imports_root / "both-fills.csv", fills)
        repeated = preview(
            exercise, imports_root, mapping, imports_root / "both-fills.csv", "fills"
        )
        assert repeated["new_events"] == 0
        apply(exercise, imports_root, repeated["batch_id"])
        final_order = {
            **orders[0],
            "state": "FILLED",
            "filled_quantity": "200",
            "event_time": at(day, 16, 0).isoformat(),
        }
        write_csv(imports_root / "final-order.csv", [final_order])
        final_batch = preview(
            exercise, imports_root, mapping, imports_root / "final-order.csv", "orders"
        )
        apply(exercise, imports_root, final_batch["batch_id"])
        view = exercise.view(at(day))
        assert (
            str(view.cash_total) == "7995.00"
            and view.positions["600000.SH"].quantity == 200
            and view.positions["600000.SH"].sellable == 0
        )
        snap = {
            "account_id": "demo",
            "mode": "demo",
            "asof": at(day).isoformat(),
            "trade_date": str(day),
            "cash_total": "7995",
            "cash_available": "7995",
            "cash_frozen": "0",
            "receivables": "0",
            "symbol": "600000.SH",
            "exchange": "XSHG",
            "quantity": "200",
            "sellable": "0",
            "frozen": "0",
        }
        write_csv(imports_root / "account.csv", [snap])
        batch = preview(
            exercise, imports_root, mapping, imports_root / "account.csv", "account_snapshot"
        )
        apply(exercise, imports_root, batch["batch_id"])
        reconciliation = reconcile(exercise, day)
        assert reconciliation["state"] == "MATCHED"
        backup = create(exercise, imports_root)
        restored_root = imports_root / "restored"
        if not (restored_root / "state" / "demo.sqlite").exists():
            restore(Path(backup["directory"]), restored_root)
        restored = Ledger(restored_root / "state" / "demo.sqlite", "demo", Mode.DEMO)
        try:
            assert restored.replay() == exercise.state()
            assert apply(restored, restored_root, repeated["batch_id"])["new_events"] == 0
        finally:
            restored.close()
        evidence = {
            "status": "PASS",
            "snapshot_id": sid,
            "research": run_result,
            "daily_sessions": len(results),
            "repeat_daily": "PASS",
            "partial_imports": imported,
            "cross_file_dedupe": "PASS",
            "reconciliation": reconciliation,
            "backup_restore": "PASS",
            "tushare_real_history": "NOT_TESTED",
            "prospective_paper": "NOT_TESTED",
            "actual_broker_mapping": "NOT_TESTED",
            "corporate_action_engine_parity": "UNSUPPORTED",
        }
        atomic_json(root / "demo-evidence.json", evidence)
        return {
            "status": "PASS",
            "evidence": str(root / "demo-evidence.json"),
            "report": str(root / "report" / "latest.html"),
            "snapshot_id": sid,
            "real_history": "NOT_TESTED",
        }
    finally:
        exercise.close()
