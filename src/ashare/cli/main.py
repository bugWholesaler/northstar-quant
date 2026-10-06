from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path

import typer
from pydantic import ValidationError

from ashare.accounting.ledger import Ledger, new_event
from ashare.adapters.data.baostock_provider import BaoStockProvider
from ashare.adapters.data.csv_provider import CSVProvider
from ashare.adapters.data.tushare_provider import TushareProvider
from ashare.config import load_config
from ashare.data.snapshot import Store, atomic_json, validate_tables
from ashare.domain.errors import AshareError
from ashare.domain.models import SH, Mode, at, canonical
from ashare.execution import importer, plans
from ashare.execution.reconcile import reconcile as reconcile_account
from ashare.ops import backup
from ashare.ops.daily import run as run_daily
from ashare.ops.locks import account_lock
from ashare.portfolio.planner import plan
from ashare.reporting.reports import daily as daily_report
from ashare.reporting.reports import status as account_status
from ashare.research.runner import run as run_research
from ashare.strategies.rules import ETFTrend, StockLowVol, aggregate

app = typer.Typer(help="Northstar · A 股研究、模拟盘与人工执行", no_args_is_help=True)
apps = {}
for name in (
    "data",
    "snapshot",
    "paper",
    "daily",
    "plan",
    "order",
    "import",
    "report",
    "risk",
    "backup",
    "account",
    "research",
):
    apps[name] = typer.Typer(no_args_is_help=True)
    app.add_typer(apps[name], name=name)


def guarded(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            value = function(*args, **kwargs)
            if value is not None:
                typer.echo(canonical(value))
        except Exception as error:
            typer.echo(
                canonical(
                    error.result()
                    if isinstance(error, AshareError)
                    else {
                        "error": "INVALID_INPUT"
                        if isinstance(
                            error, (ValidationError, ValueError, FileNotFoundError, KeyError)
                        )
                        else "RUNTIME_ERROR",
                        "message": str(error),
                    }
                ),
                err=True,
            )
            raise typer.Exit(2) from None

    return call


def configuration(account="paper", config=None, root=None):
    path = Path(config or f"configs/{account}.yaml")
    result = load_config(path)
    if account != result.account_id:
        raise AshareError("MODE_ISOLATION", "account differs from config")
    return result.model_copy(update={"runtime": str(root)}) if root else result


@contextmanager
def session(account="paper", config=None, root=None):
    cfg = configuration(account, config, root)
    directory = Path(cfg.runtime).resolve()
    with account_lock(directory, account):
        ledger = Ledger(directory / "state" / f"{account}.sqlite", account, cfg.mode)
        try:
            yield cfg, directory, ledger
        finally:
            ledger.close()


def selected_snapshot(root, ledger, sid):
    if sid:
        return sid
    row = ledger.db.execute(
        "SELECT snapshot_id FROM daily_inputs ORDER BY day DESC LIMIT 1"
    ).fetchone()
    if row:
        return row[0]
    raise AshareError("SNAPSHOT_REQUIRED", "pass --snapshot ID")


@app.command()
@guarded
def demo(scenario: str = "complete", root: Path = Path("runtime/demo")):
    if scenario != "complete":
        raise AshareError("SCENARIO", "supported scenario: complete")
    from ashare.demo import run

    with account_lock(root, "demo"):
        return run(root.resolve())


@app.command()
@guarded
def status(
    account: str = "paper",
    date_: str = typer.Option(None, "--date"),
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (_, directory, ledger):
        day = date.fromisoformat(date_) if date_ else datetime.now(SH).date()
        result = account_status(ledger, day)
        daily_report(ledger, directory, day)
        return result


@app.command()
@guarded
def backtest(
    config: Path = Path("configs/demo.yaml"),
    snapshot: str = typer.Option(...),
    start: str = None,
    end: str = None,
    strategy: str = "combined",
    split: str = "synthetic",
    capital: str = None,
    cost_multiplier: str = "1",
    delay_days: int = 1,
    root: Path = None,
):
    cfg = load_config(config)
    if cfg.mode in (Mode.MANUAL, Mode.PAPER):
        raise AshareError("MODE_ISOLATION", "historical backtest requires research/demo config")
    directory = Path(root or cfg.runtime).resolve()
    cfg = cfg.model_copy(update={"runtime": str(directory)})
    snap, _ = Store(directory).load(snapshot)
    final = date.fromisoformat(end) if end else snap.end
    first = date.fromisoformat(start) if start else max(snap.start, final - timedelta(days=90))
    return run_research(
        directory,
        cfg,
        snapshot,
        first,
        final,
        strategy,
        Decimal(capital) if capital else None,
        Decimal(cost_multiplier),
        delay_days,
        split,
    )


@apps["data"].command()
@guarded
def probe(provider: str = "tushare", root: Path = Path("runtime")):
    if provider == "baostock":
        return BaoStockProvider().probe(root.resolve())
    if provider != "tushare":
        raise AshareError("PROVIDER", "registered probe providers: tushare, baostock")
    result = TushareProvider().probe()
    atomic_json(root / "data-capabilities.json", result)
    return result


@apps["data"].command("sync")
@guarded
def data_sync(
    config: Path = Path("configs/research.yaml"), directory: Path = None, root: Path = None
):
    cfg = load_config(config)
    directory = directory or (Path(cfg.csv_directory) if cfg.csv_directory else None)
    if cfg.provider == "baostock":
        return BaoStockProvider(timeout=cfg.baostock.timeout).sync(
            cfg, Path(root or cfg.runtime).resolve()
        )
    if cfg.provider == "tushare":
        return TushareProvider().sync(cfg)
    if directory is None:
        raise AshareError("CSV_DIRECTORY", "pass --directory canonical CSV folder")
    return CSVProvider().ingest(
        Path(root or cfg.runtime).resolve(), directory, cfg.scope, cfg.availability
    )


@apps["data"].command("fetch")
@guarded
def data_fetch(
    dataset: str = typer.Option(...),
    parameters: Path = typer.Option(...),
    root: Path = Path("runtime"),
    page_size: int = 1000,
    refresh: bool = False,
    provider: str = "tushare",
):
    if provider == "baostock":
        result = BaoStockProvider().fetch(
            root.resolve(), dataset, json.loads(parameters.read_text()), refresh
        )
        return {
            key: result[key]
            for key in ("request_id", "observed_at", "fields", "directory", "cached")
        } | {"rows": len(result["rows"])}
    if provider != "tushare":
        raise AshareError("PROVIDER", "unknown provider")
    result = TushareProvider().fetch_pages(
        root, dataset, json.loads(parameters.read_text()), page_size, refresh=refresh
    )
    return {k: v for k, v in result.items() if k != "rows"}


@apps["data"].command("validate")
@guarded
def data_validate(batch: str = typer.Option(...), root: Path = Path("runtime/research")):
    metadata = json.loads((root / "batches" / (batch + ".json")).read_text())
    _, tables = Store(root).load(metadata["snapshot_id"])
    return validate_tables(tables)


@apps["snapshot"].command("create")
@guarded
def snapshot_create(batch: str = typer.Option(...), root: Path = Path("runtime/research")):
    metadata = json.loads((root / "batches" / (batch + ".json")).read_text())
    snap, _ = Store(root).load(metadata["snapshot_id"])
    return snap


@apps["daily"].command("run")
@guarded
def daily_run(
    account: str = "paper",
    date_: str = typer.Option(..., "--date"),
    snapshot: str = None,
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (cfg, directory, ledger):
        return run_daily(
            ledger,
            directory,
            cfg,
            date.fromisoformat(date_),
            snapshot,
        )


@apps["paper"].command("step")
@guarded
def paper_step(
    date_: str = typer.Option(..., "--date"),
    snapshot: str = None,
    config: Path = None,
    root: Path = None,
):
    # Exactly the same daily orchestrator.
    with session("paper", config, root) as (cfg, directory, ledger):
        return run_daily(
            ledger,
            directory,
            cfg,
            date.fromisoformat(date_),
            snapshot,
        )


@apps["plan"].command("build")
@guarded
def plan_build(
    account: str = "paper",
    asof: str = typer.Option(...),
    snapshot: str = None,
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (cfg, directory, ledger):
        sid = selected_snapshot(directory, ledger, snapshot)
        snap, tables = Store(directory).load(sid)
        day = date.fromisoformat(asof)
        if snap.end < day:
            raise AshareError("DATA_STALE", "snapshot lacks decision-day data")
        if cfg.mode in (Mode.PAPER, Mode.MANUAL) and (
            day != datetime.now(SH).date() or datetime.now(SH) < at(day)
        ):
            raise AshareError("DECISION_TIME", "live planning requires current day after 21:30")
        view = MarketView(snap, tables, at(day))
        target = aggregate(
            [ETFTrend(cfg).generate(view, day), StockLowVol(cfg).generate(view, day)]
        )
        if target is None:
            return {"state": "NO_REBALANCE", "day": asof}
        return plans.save(ledger, directory, plan(target, view, ledger.view(at(day)), cfg))


from ashare.data.snapshot import MarketView  # noqa: E402


@apps["plan"].command("preflight")
@guarded
def plan_preflight(
    plan: str = typer.Option(...),
    account: str = "manual",
    confirmation: Path = None,
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (cfg, directory, ledger):
        obj = plans.get(ledger, plan)
        snap, tables = Store(directory).load(obj["snapshot_id"])
        result = plans.preflight(
            ledger,
            cfg,
            plan,
            snap,
            tables,
            json.loads(confirmation.read_text()) if confirmation else None,
        )
        if result["state"] == "BLOCKED":
            raise AshareError("PREFLIGHT_BLOCKED", "preflight did not pass", **result)
        return result


@apps["plan"].command("export")
@guarded
def plan_export(
    plan: str = typer.Option(...), account: str = "manual", config: Path = None, root: Path = None
):
    with session(account, config, root) as (_, directory, ledger):
        return plans.export(ledger, directory, plan)


@apps["order"].command("register")
@guarded
def order_register(
    intent: str = typer.Option(...),
    broker_order_id: str = "",
    state: str = "SUBMITTED",
    account: str = "manual",
    config: Path = None,
    root: Path = None,
):
    if state != "UNKNOWN" and not broker_order_id:
        raise AshareError("ORDER_ID_REQUIRED", "broker order id required unless state UNKNOWN")
    with session(account, config, root) as (_, _, ledger):
        return plans.register(ledger, intent, broker_order_id, state)


@apps["import"].command("preview")
@guarded
def import_preview(
    account: str = "manual",
    mapping: Path = typer.Option(...),
    file: Path = typer.Option(...),
    kind: str = None,
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (_, directory, ledger):
        result = importer.preview(ledger, directory, mapping, file, kind)
        return {k: result[k] for k in ("batch_id", "kind", "new_events", "projected")}


@apps["import"].command("apply")
@guarded
def import_apply(batch: str, account: str = "manual", config: Path = None, root: Path = None):
    with session(account, config, root) as (_, directory, ledger):
        return importer.apply(ledger, directory, batch)


@app.command("reconcile")
@guarded
def reconciliation(
    account: str = "manual",
    date_: str = typer.Option(..., "--date"),
    operator: str = None,
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (_, _, ledger):
        return reconcile_account(ledger, date.fromisoformat(date_), operator)


@apps["report"].command("daily")
@guarded
def report_daily(
    account: str = "paper",
    date_: str = typer.Option(..., "--date"),
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (_, directory, ledger):
        return daily_report(ledger, directory, date.fromisoformat(date_))


@apps["risk"].command("halt")
@guarded
def risk_halt(
    account: str = "paper", reason: str = typer.Option(...), config: Path = None, root: Path = None
):
    with session(account, config, root) as (_, _, ledger):
        ledger.halt(reason)
        return {"risk_state": "HALTED", "reason": reason, "orders": ledger.state()["orders"]}


@apps["risk"].command("resume")
@guarded
def risk_resume(
    account: str = "paper",
    operator: str = typer.Option(...),
    reason: str = typer.Option(...),
    config: Path = None,
    root: Path = None,
):
    with session(account, config, root) as (_, _, ledger):
        ledger.resume(operator, reason)
        return {"risk_state": ledger.meta("risk_state")}


@apps["backup"].command("create")
@guarded
def backup_create(account: str = "paper", config: Path = None, root: Path = None):
    with session(account, config, root) as (_, directory, ledger):
        return backup.create(ledger, directory)


@apps["backup"].command("verify")
@guarded
def backup_verify(backup_: Path = typer.Option(..., "--backup")):
    return backup.verify(backup_)


@apps["backup"].command("restore")
@guarded
def backup_restore(backup_: Path = typer.Option(..., "--backup"), root: Path = typer.Option(...)):
    return backup.restore(backup_, root)


@apps["account"].command("init")
@guarded
def account_init(
    account: str = "manual",
    cash: str = typer.Option(...),
    positions: Path = None,
    asof: str = typer.Option(...),
    config: Path = None,
    root: Path = None,
):
    timestamp = datetime.fromisoformat(asof)
    if timestamp.tzinfo is None:
        raise AshareError("TIMESTAMP", "initialization timestamp needs timezone")
    with session(account, config, root) as (_, _, ledger):
        payload = {
            "cash": str(cash),
            "positions": json.loads(positions.read_text()) if positions else [],
        }
        ledger.append(
            [
                new_event(
                    account,
                    ledger.mode,
                    "AccountInitialized",
                    timestamp.date(),
                    payload,
                    "operator",
                    key="initialization",
                    event_time=timestamp,
                )
            ]
        )
        return ledger.view(timestamp)


@apps["research"].command("suite")
@guarded
def research_suite(
    snapshot: str = typer.Option(...),
    start: str = typer.Option(...),
    end: str = typer.Option(...),
    config: Path = Path("configs/demo.yaml"),
    split: str = "synthetic",
    root: Path = None,
):
    from ashare.research.runner import suite

    cfg = load_config(config)
    if cfg.mode not in (Mode.DEMO, Mode.RESEARCH):
        raise AshareError("MODE_ISOLATION", "suite requires isolated research/demo config")
    return suite(
        Path(root or cfg.runtime).resolve(),
        cfg,
        snapshot,
        date.fromisoformat(start),
        date.fromisoformat(end),
        split,
    )


@apps["research"].command("prepare-free")
@guarded
def prepare_free(config: Path = Path("configs/free_backtest.yaml"), root: Path = None):
    from ashare.data.free_backtest_snapshot import build

    cfg = load_config(config)
    return build(Path(root or cfg.runtime).resolve(), cfg)


@apps["research"].command("trial-free")
@guarded
def trial_free(
    snapshot: str = typer.Option(...),
    start: str = "2026-01-30",
    end: str = "2026-09-30",
    config: Path = Path("configs/free_backtest.yaml"),
    root: Path = None,
):
    from ashare.research.free_trial import run_trial

    cfg = load_config(config)
    return run_trial(
        Path(root or cfg.runtime).resolve(),
        cfg,
        snapshot,
        date.fromisoformat(start),
        date.fromisoformat(end),
    )
