import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from ashare.accounting.ledger import Ledger
from ashare.data.snapshot import publish
from ashare.domain.errors import AshareError
from ashare.domain.models import Mode, at
from ashare.ops.backup import create, restore, verify
from ashare.ops.daily import run as daily
from ashare.ops.locks import account_lock
from ashare.reporting.reports import status


def test_transaction_crash_rollback(ledger):
    code = """import os,sys,sqlite3
conn=sqlite3.connect(sys.argv[1])
conn.execute("BEGIN IMMEDIATE")
conn.execute("INSERT INTO metadata VALUES('crash-marker','must-not-survive')")
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", code, str(ledger.path)], check=False)
    assert result.returncode == 17
    assert ledger.meta("crash-marker") is None
    assert ledger.replay() == ledger.state()


def test_backup_restore_replay_tamper(tmp_path, ledger):
    backup = create(ledger, tmp_path)
    directory = Path(backup["directory"])
    assert verify(directory)["state"] == "VERIFIED"
    target = tmp_path / "restored"
    restore(directory, target)
    restored = Ledger(target / "state" / "demo.sqlite", "demo", Mode.DEMO)
    try:
        assert restored.replay() == ledger.state()
        assert len(restored.events()) == len(ledger.events())
    finally:
        restored.close()
    with pytest.raises(AshareError):
        restore(directory, target)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"]["ledger.sqlite"] = "wrong-hash"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(AshareError):
        verify(directory)


def test_account_lock_excludes_another_writer(tmp_path):
    with account_lock(tmp_path, "demo"):
        with pytest.raises(AshareError, match="writer"):
            with account_lock(tmp_path, "demo"):
                pass


def test_daily_restart_matches_continuous_and_repeated_day(market, config):
    root, snapshot, tables = market
    days = [
        c.trade_date
        for c in tables["calendar"]
        if c.exchange == "XSHG"
        and c.is_open
        and date(2025, 4, 23) <= c.trade_date <= date(2025, 5, 6)
    ]
    # Same config identity and input produce the same economic facts across restarts.
    results = []
    for name, restart in (("continuous", False), ("restart", True)):
        sub = root / name
        publish(sub, tables, "synthetic", "synthetic_sample", "historical_assumption")
        ledger = Ledger(sub / "state" / "demo.sqlite", "demo", Mode.DEMO)
        for index, day in enumerate(days):
            daily(ledger, sub, config, day, snapshot.snapshot_id)
            if restart and index == 4:
                ledger.close()
                ledger = Ledger(sub / "state" / "demo.sqlite", "demo", Mode.DEMO)
        count = len(ledger.events())
        repeated = daily(ledger, sub, config, days[-1], snapshot.snapshot_id)
        assert repeated["repeated"] and len(ledger.events()) == count
        results.append((ledger.replay(), [e.model_dump(mode="json") for e in ledger.events()]))
        ledger.close()
    assert results[0] == results[1]


def test_revised_past_data_halts_daily(market, config):
    from decimal import Decimal

    root, snapshot, tables = market
    ledger = Ledger(root / "state" / "demo.sqlite", "demo", Mode.DEMO)
    try:
        daily(ledger, root, config, date(2025, 4, 29), snapshot.snapshot_id)
        changed = {
            **tables,
            "bars": [
                b.model_copy(update={"close": b.close + Decimal("0.001")})
                if b.trade_date == date(2025, 4, 29) and b.instrument_id == "510300.SH"
                else b
                for b in tables["bars"]
            ],
        }
        new = publish(root, changed, "synthetic", "synthetic_sample", "historical_assumption")
        with pytest.raises(AshareError, match="already published"):
            daily(ledger, root, config, date(2025, 4, 30), new.snapshot_id)
        assert ledger.meta("risk_state") == "HALTED"
    finally:
        ledger.close()


def test_missed_day_does_not_backdate_trade(market, config):
    root, snapshot, tables = market
    ledger = Ledger(root / "state" / "demo.sqlite", "demo", Mode.DEMO)
    try:
        daily(ledger, root, config, date(2025, 4, 30), snapshot.snapshot_id)
        before = len(ledger.events())
        with pytest.raises(AshareError, match="backdate"):
            daily(ledger, root, config, date(2025, 5, 5), snapshot.snapshot_id)
        assert len(ledger.events()) == before
    finally:
        ledger.close()


def test_closed_day_and_stale_success_are_distinct(market, config):
    root, snapshot, _ = market
    ledger = Ledger(root / "state" / "demo.sqlite", "demo", Mode.DEMO)
    try:
        result = daily(ledger, root, config, date(2025, 5, 1), snapshot.snapshot_id)
        assert result["job_status"] == "NOOP_MARKET_CLOSED" and not ledger.events()
        current = status(ledger, date(2025, 5, 2))
        assert current["job_status"] == "TASK_MISSING" and current["effective_date"] == "2025-05-01"
    finally:
        ledger.close()


def test_daily_failure_rolls_back_new_facts_and_can_retry(market, config, monkeypatch):
    import ashare.ops.daily as module

    root, snapshot, _ = market
    ledger = Ledger(root / "state" / "demo.sqlite", "demo", Mode.DEMO)
    try:
        daily(ledger, root, config, date(2025, 4, 30), snapshot.snapshot_id)
        before = ledger.state()
        count = len(ledger.events())
        original = module.save

        def fail(*args, **kwargs):
            raise RuntimeError("injected failure after engine fills before checkpoint")

        monkeypatch.setattr(module, "save", fail)
        with pytest.raises(RuntimeError):
            daily(ledger, root, config, date(2025, 5, 2), snapshot.snapshot_id)
        assert ledger.state() == before and len(ledger.events()) == count
        assert status(ledger, date(2025, 5, 2))["job_status"] == "FAILED"
        monkeypatch.setattr(module, "save", original)
        result = daily(ledger, root, config, date(2025, 5, 2), snapshot.snapshot_id)
        assert result["job_status"] == "SUCCESS"
        assert len(ledger.events()) > count
        ledger.replay()
    finally:
        ledger.close()


def test_observed_paper_closed_day_does_not_require_decision_window(tmp_path):
    from ashare.config import Config
    from ashare.data.synthetic import generate

    tables = generate(tmp_path / "input", observed=True)
    snapshot = publish(tmp_path, tables, "synthetic", "synthetic_sample", "observed")
    config = Config(
        mode=Mode.PAPER, account_id="paper", availability="observed", scope="synthetic_sample"
    )
    ledger = Ledger(tmp_path / "state" / "paper.sqlite", "paper", Mode.PAPER)
    try:
        result = daily(
            ledger,
            tmp_path,
            config,
            date(2025, 5, 1),
            snapshot.snapshot_id,
            now=at(date(2025, 5, 1), 10, 0),
        )
        assert result["job_status"] == "NOOP_MARKET_CLOSED" and not ledger.events()
    finally:
        ledger.close()


def test_process_death_during_economic_batch_does_not_commit_half(ledger):
    code = """import os,sys
from datetime import date
from ashare.accounting.ledger import Ledger,new_event
from ashare.domain.models import Mode
import ashare.accounting.ledger as module
l=Ledger(__import__('pathlib').Path(sys.argv[1]),'demo',Mode.DEMO)
original=module.reduce_event
def injected(state,event):
    original(state,event)
    if event.kind=='Fill':
        os._exit(17)
module.reduce_event=injected
payload={'symbol':'600000.SH','side':'BUY','quantity':100,'price':'10','gross_amount':'1000','commission':'5','tax':'0','other_fees':'0','sellable_on':'2025-05-02'}
l.append([new_event('demo',Mode.DEMO,'Fill',date(2025,4,30),payload,key='crash-fill'),new_event('demo',Mode.DEMO,'Deposit',date(2025,5,2),{'amount':'100'},key='crash-deposit')])
"""
    before = ledger.state()
    result = subprocess.run([sys.executable, "-c", code, str(ledger.path)], check=False)
    assert result.returncode == 17
    assert len(ledger.events()) == 1 and ledger.state() == before and ledger.replay() == before
