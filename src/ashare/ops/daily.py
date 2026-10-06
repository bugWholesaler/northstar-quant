from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ashare.adapters.backtest.rqalpha_engine import RQAlphaEngine
from ashare.data.snapshot import MarketView, Store, atomic_json
from ashare.domain.errors import AshareError
from ashare.domain.models import (
    SH,
    ExecutionEvent,
    ExperimentSpec,
    Mode,
    TargetPortfolio,
    at,
    canonical,
    stable_id,
)
from ashare.execution.plans import save
from ashare.execution.reconcile import reconcile
from ashare.ops.backup import create as backup
from ashare.portfolio.planner import plan
from ashare.reporting.reports import daily as report
from ashare.strategies.rules import ETFTrend, StockLowVol, aggregate


def fingerprint(tables, day):
    return stable_id(
        {
            "bars": sorted(
                [b.model_dump(mode="json") for b in tables["bars"] if b.trade_date == day],
                key=canonical,
            ),
            "calendar": [
                c.model_dump(mode="json") for c in tables["calendar"] if c.trade_date == day
            ],
            "instruments": [i.model_dump(mode="json") for i in tables["instruments"]],
            "actions": [a.model_dump(mode="json") for a in tables["actions"] if a.ex_date <= day],
        }
    )


def _run(ledger, root: Path, config, day, sid, now=None):
    now = now or datetime.now(SH)
    if ledger.mode in (Mode.PAPER, Mode.MANUAL) and day != now.date():
        raise AshareError(
            "HISTORICAL_DAILY_FORBIDDEN",
            "paper/manual daily is prospective; historical rehearsal uses demo/research",
        )
    jid = stable_id({"day": day, "account": ledger.account_id, "config": config, "job": "daily"})
    if sid is None:
        if config.csv_directory:
            from ashare.adapters.data.csv_provider import CSVProvider

            sid = CSVProvider().ingest(
                root, Path(config.csv_directory), config.scope, config.availability
            )["snapshot_id"]
        else:
            last_snapshot = ledger.db.execute(
                "SELECT snapshot_id FROM daily_inputs ORDER BY day DESC LIMIT 1"
            ).fetchone()
            if last_snapshot:
                sid = last_snapshot[0]
            else:
                result = {
                    "job_id": jid,
                    "day": str(day),
                    "job_status": "WAITING_DATA",
                    "data_status": "INCOMPLETE",
                    "reason": "set csv_directory or pass --snapshot; no silent provider fallback",
                }
                with ledger.db:
                    ledger.db.execute(
                        "INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)",
                        (jid, str(day), "WAITING_DATA", canonical(result)),
                    )
                return {**result, "report": report(ledger, root, day)}
    snapshot, tables = Store(root).load(sid)
    if snapshot.profile != "verified":
        raise AshareError(
            "SNAPSHOT_PROFILE", "scenario snapshot is not a prospective execution input"
        )
    if ledger.mode == Mode.PAPER and snapshot.availability != "observed":
        raise AshareError("OBSERVATION_REQUIRED", "paper requires true first-observed timestamps")
    for record_day, old_hash in ledger.db.execute("SELECT day,hash FROM daily_inputs"):
        if fingerprint(tables, __import__("datetime").date.fromisoformat(record_day)) != old_hash:
            ledger.halt("published paper input revised")
            raise AshareError(
                "HISTORICAL_REVISION",
                "new snapshot changes already published daily input",
                day=record_day,
            )
    from ashare.research.runner import code_identity

    identity = code_identity()
    execution_code = stable_id({"source": identity["source_hash"], "lock": identity["lock_hash"]})
    execution_config = stable_id(
        config.model_dump(mode="json", exclude={"runtime", "csv_directory"})
    )
    if ledger.mode in (Mode.DEMO, Mode.PAPER):
        if ledger.meta("paper_execution_code") not in (None, execution_code) or ledger.meta(
            "paper_execution_config"
        ) not in (None, execution_config):
            ledger.halt("published paper stream code/config changed; explicit migration required")
            raise AshareError(
                "PAPER_VERSION_CHANGED",
                "do not redefine frozen paper history with new code or execution parameters",
            )
    previous = ledger.db.execute("SELECT state,body FROM jobs WHERE job_id=?", (jid,)).fetchone()
    if previous and previous[0] in ("SUCCESS", "NOOP_MARKET_CLOSED"):
        return {**json.loads(previous[1]), "repeated": True, "report": report(ledger, root, day)}
    calendars = [c for c in tables["calendar"] if c.trade_date == day]
    if len(calendars) != 2:
        raise AshareError("CALENDAR_RANGE", "requested date absent from both exchange calendars")
    if not any(c.is_open for c in calendars):
        result = {
            "job_id": jid,
            "day": str(day),
            "job_status": "NOOP_MARKET_CLOSED",
            "data_status": "READY",
            "snapshot_id": sid,
            "data_scope": snapshot.scope,
        }
        with ledger.db:
            ledger.db.execute(
                "INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)",
                (jid, str(day), result["job_status"], canonical(result)),
            )
        return {**result, "report": report(ledger, root, day), "backup": backup(ledger, root)}
    if ledger.mode in (Mode.PAPER, Mode.MANUAL) and now < at(day):
        raise AshareError("DECISION_TIME", "daily planning waits until 21:30 Shanghai")
    bars = [b for b in tables["bars"] if b.trade_date == day]
    if not bars or snapshot.end < day or any(b.available_at > now for b in bars):
        result = {
            "job_id": jid,
            "day": str(day),
            "job_status": "WAITING_DATA",
            "data_status": "INCOMPLETE",
            "snapshot_id": sid,
            "data_scope": snapshot.scope,
        }
        with ledger.db:
            ledger.db.execute(
                "INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)",
                (jid, str(day), result["job_status"], canonical(result)),
            )
        return {**result, "report": report(ledger, root, day)}
    last = ledger.db.execute("SELECT MAX(day) FROM daily_inputs").fetchone()[0]
    if last:
        missing = [
            c.trade_date
            for c in tables["calendar"]
            if c.exchange == "XSHG" and c.is_open and last < str(c.trade_date) < str(day)
        ]
        if missing:
            ledger.halt("missed prospective trading day; frozen state requires review")
            raise AshareError(
                "MISSED_SESSION",
                "never backdate unrecorded paper trades",
                missing=[str(d) for d in missing],
            )
        if str(day) < last:
            raise AshareError("TIME_REWIND", "cannot move published daily account backwards")
    with ledger.db:
        ledger.db.execute(
            "INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)",
            (jid, str(day), "RUNNING", canonical({"data_status": "READY", "snapshot_id": sid})),
        )
    atomic_json(root / "configurations" / (stable_id(config) + ".json"), config)
    try:
        result = {
            "job_id": jid,
            "day": str(day),
            "job_status": "SUCCESS",
            "data_status": "READY",
            "snapshot_id": sid,
            "data_scope": snapshot.scope,
        }
        new_events = []
        risk_review = False
        first = None
        if ledger.mode in (Mode.DEMO, Mode.PAPER):
            if ledger.meta("risk_state") != "RUNNING":
                result["job_status"] = "PARTIAL"
                result["reason"] = "risk halted; simulation/planning suppressed"
            else:
                first = ledger.meta("paper_first_day") or str(day)
                frozen = json.loads(ledger.meta("paper_frozen_plans") or "{}")
                spec = ExperimentSpec(
                    strategy="combined",
                    snapshot_id=sid,
                    start=first,
                    end=day,
                    capital=config.initial_cash,
                    config_hash=stable_id(config),
                    split="synthetic" if snapshot.scope == "synthetic_sample" else "development",
                )
                output = RQAlphaEngine().evaluate(spec, snapshot, tables, config, root, frozen)
                events = [ExecutionEvent.model_validate(e) for e in output["events"]]
                prefix = ledger.events()
                if events[: len(prefix)] != prefix:
                    ledger.halt("engine replay prefix diverged")
                    raise AshareError(
                        "PAPER_PREFIX_DIVERGED", "frozen prior events do not match replay"
                    )
                new_events = events[len(prefix) :]
                from ashare.domain.models import decimal

                peak = max(decimal(row["nav"]) for row in output["equity"])
                risk_review = (
                    peak > 0
                    and 1 - decimal(output["equity"][-1]["nav"]) / peak
                    >= config.risk.drawdown_review
                )
                atomic_json(
                    root / "paper" / ledger.account_id / str(day) / "engine.json",
                    {
                        k: output[k]
                        for k in ("equity", "comparisons", "effective", "fills", "events")
                    },
                )
        else:
            result["reconciliation"] = reconcile(ledger, day)["state"]
            if result["reconciliation"] != "MATCHED":
                result["job_status"] = "PARTIAL"
        # Old facts and reconciliation precede new strategy targets.
        view = MarketView(snapshot, tables, at(day))
        target = None
        if view.is_month_end(day):
            target = aggregate(
                [ETFTrend(config).generate(view, day), StockLowVol(config).generate(view, day)]
            )
        elif ledger.meta("active_target"):
            target = TargetPortfolio.model_validate_json(ledger.meta("active_target"))
        with ledger.db:
            if ledger.mode in (Mode.DEMO, Mode.PAPER) and first:
                ledger.append(new_events, inside_transaction=True)
                ledger.set_meta("paper_first_day", first)
                ledger.set_meta("paper_execution_code", execution_code)
                ledger.set_meta("paper_execution_config", execution_config)
                ledger.set_meta("paper_code_identity", canonical(identity))
                ledger.set_meta("reconciliation", "MATCHED")
                if risk_review:
                    ledger.set_meta("risk_state", "REVIEW")
                    ledger.set_meta(
                        "review_reason", "drawdown threshold reached; no automatic liquidation"
                    )
            if target and target.expires_at > day and ledger.meta("risk_state") == "RUNNING":
                future = view.next_days(day, 1)[0]
                if target.trade_not_before <= future <= target.expires_at:
                    decision_view = MarketView(snapshot, tables, target.decision_at)
                    draft = plan(target, decision_view, ledger.view(at(day)), config, future)
                    save(ledger, root, draft, inside_transaction=True)
                    result["plan_id"], result["plan_state"] = draft["plan_id"], draft["state"]
                    if ledger.mode in (Mode.DEMO, Mode.PAPER):
                        frozen = json.loads(ledger.meta("paper_frozen_plans") or "{}")
                        frozen[str(future)] = draft
                        ledger.set_meta("paper_frozen_plans", canonical(frozen))
                    ledger.set_meta("active_target", canonical(target))
            ledger.db.execute(
                "INSERT OR REPLACE INTO daily_inputs VALUES(?,?,?)",
                (str(day), sid, fingerprint(tables, day)),
            )
            ledger.db.execute(
                "UPDATE jobs SET state=?,body=? WHERE job_id=?",
                (result["job_status"], canonical(result), jid),
            )
        return {**result, "report": report(ledger, root, day), "backup": backup(ledger, root)}
    except Exception as error:
        with ledger.db:
            ledger.db.execute(
                "UPDATE jobs SET state='FAILED',body=? WHERE job_id=?",
                (
                    canonical(
                        {
                            "error": getattr(error, "code", "RUNTIME"),
                            "data_status": "READY",
                            "message": str(error),
                        }
                    ),
                    jid,
                ),
            )
        report(ledger, root, day)
        raise


def run(ledger, root: Path, config, day, sid, now=None):
    try:
        return _run(ledger, root, config, day, sid, now)
    except Exception as error:
        ledger.db.rollback()
        jid = stable_id(
            {"day": day, "account": ledger.account_id, "config": config, "job": "daily"}
        )
        with ledger.db:
            ledger.db.execute(
                "INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)",
                (
                    jid,
                    str(day),
                    "FAILED",
                    canonical(
                        {
                            "error": getattr(error, "code", "RUNTIME"),
                            "message": str(error),
                            "data_status": "INCOMPLETE",
                        }
                    ),
                ),
            )
        if isinstance(error, OSError):
            ledger.halt("operational failure: " + type(error).__name__)
        report(ledger, root, day)
        raise
