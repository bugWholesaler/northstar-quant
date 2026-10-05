from __future__ import annotations

import csv
import platform
import subprocess
from datetime import datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from statistics import mean, stdev

from ashare.adapters.backtest.rqalpha_engine import RQAlphaEngine
from ashare.data.snapshot import Store, atomic_json, file_hash
from ashare.domain.errors import AshareError
from ashare.domain.models import ExperimentSpec, decimal, stable_id
from ashare.reporting.reports import research_report


def code_identity():
    repo = Path(__file__).resolve().parents[3]
    files = sorted((repo / "src").rglob("*.py")) + [repo / "pyproject.toml"]
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except subprocess.CalledProcessError:
        commit = "UNCOMMITTED"
    try:
        diff = subprocess.check_output(
            ["git", "diff", "HEAD", "--", "src", "pyproject.toml", "uv.lock"],
            cwd=repo,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        diff = b"uncommitted"
    import hashlib

    return {
        "git_commit": commit,
        "dirty_diff_hash": hashlib.sha256(diff).hexdigest(),
        "source_hash": stable_id({str(p.relative_to(repo)): file_hash(p) for p in files}),
        "lock_hash": file_hash(repo / "uv.lock") if (repo / "uv.lock").exists() else "MISSING",
        "python": platform.python_version(),
        "engine_version": version("rqalpha"),
    }


def metrics(output, capital, annual_cost=Decimal(0)):
    values = [float(decimal(row["nav"])) for row in output["equity"]]
    returns = [b / a - 1 for a, b in zip([float(capital)] + values[:-1], values)]
    peak, drawdown = float(capital), 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    fees = sum(decimal(f[k]) for f in output["fills"] for k in ("commission", "tax", "other_fees"))
    turnover = sum(decimal(f["gross_amount"]) for f in output["fills"])
    volatility = stdev(returns) * 252**0.5 if len(returns) > 1 else 0.0
    total = values[-1] / float(capital) - 1 if values else 0
    yearly = {}
    previous = float(capital)
    for row in output["equity"]:
        year = row["date"][:4]
        entry = yearly.setdefault(year, {"start_nav": previous, "end_nav": previous})
        entry["end_nav"] = float(decimal(row["nav"]))
        previous = entry["end_nav"]
    for entry in yearly.values():
        entry["return"] = entry["end_nav"] / entry["start_nav"] - 1
    ordered_quantity = sum(i["quantity"] for i in output["orders"])
    filled_quantity = sum(f["quantity"] for f in output["fills"])
    return {
        "total_return": total,
        "yearly": yearly,
        "mean_cash_weight": mean(
            float(decimal(e["cash"]) / decimal(e["nav"])) for e in output["equity"]
        )
        if values
        else None,
        "unfilled_quantity_fraction": 1 - filled_quantity / ordered_quantity
        if ordered_quantity
        else None,
        "annualized_return": (1 + total) ** (252 / len(values)) - 1 if len(values) >= 252 else None,
        "max_drawdown": drawdown,
        "annualized_volatility": volatility,
        "sharpe": mean(returns) / stdev(returns) * 252**0.5
        if len(returns) > 1 and stdev(returns)
        else None,
        "risk_free_rate": 0,
        "frequency": "daily",
        "sessions": len(values),
        "fees": str(fees),
        "fee_breakdown": {
            key: str(sum(decimal(fill[key]) for fill in output["fills"]))
            for key in ("commission", "tax", "other_fees")
        },
        "turnover_two_sided": float(turnover / decimal(mean(values))) if values else 0,
        "fills": len(output["fills"]),
        "rejected_orders": len(output["rejections"]),
        "fixed_expense": str(annual_cost * len(values) / 252),
        "economic_pnl": str(decimal(values[-1]) - capital - annual_cost * len(values) / 252)
        if values
        else "0",
        "cash_benchmark_return": 0,
        "benchmark_comparability": "Cash benchmark; passive ETF/stock comparisons require separate runs",
    }


def run(
    root: Path,
    config,
    snapshot_id,
    start,
    end,
    strategy="combined",
    capital=None,
    cost_multiplier=Decimal(1),
    delay_days=1,
    split="synthetic",
):
    snapshot, tables = Store(root).load(snapshot_id)
    if snapshot.scope == "synthetic_sample" and split != "synthetic":
        raise AshareError("SPLIT_SCOPE", "synthetic data must use synthetic split")
    if snapshot.scope != "synthetic_sample" and split == "synthetic":
        raise AshareError(
            "SPLIT_SCOPE", "real data requires registered development/validation split"
        )
    if split == "unlocked_holdout":
        raise AshareError(
            "HOLDOUT_LOCKED",
            "OS-isolated holdout runner not implemented; do not claim clean holdout",
        )
    spec = ExperimentSpec(
        strategy=strategy,
        snapshot_id=snapshot_id,
        start=start,
        end=end,
        capital=capital or config.initial_cash,
        cost_multiplier=cost_multiplier,
        delay_days=delay_days,
        config_hash=stable_id(config),
        split=split,
    )
    identity = {
        "experiment": spec.model_dump(mode="json"),
        "code": code_identity(),
        "config": config.model_dump(mode="json"),
        "snapshot": snapshot.model_dump(mode="json"),
        "engine": "RQAlpha",
    }
    rid = stable_id(identity)
    directory = root / "runs" / rid
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        **identity,
        "run_id": rid,
        "status": "RUNNING",
        "started_at": datetime.now().astimezone().isoformat(),
        "assumptions": [
            "日频收盘成交代理，与人工执行窗口不同",
            "固定 ETF 白名单有条件选择偏差",
            "情景费率，尚非实际券商费率",
            "ETF 穿透暴露未知",
            "synthetic"
            if snapshot.scope == "synthetic_sample"
            else "历史可用时间可能为推定；全市场范围未验证",
        ],
    }
    atomic_json(directory / "manifest.json", manifest)
    try:
        output = RQAlphaEngine().evaluate(spec, snapshot, tables, config, root)
        summary = metrics(output, spec.capital, config.fixed_annual_cost)
        for name in ("equity", "fills", "orders", "targets", "comparisons", "rejections"):
            atomic_json(directory / (name + ".json"), output[name])
        atomic_json(directory / "events.json", output["events"])
        atomic_json(directory / "plans.json", output["plans"])
        atomic_json(directory / "requested-config.json", output["requested"])
        atomic_json(directory / "effective-config.json", output["effective"])
        atomic_json(directory / "metrics.json", summary)
        with (directory / "equity.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, ["date", "nav", "cash", "positions"])
            writer.writeheader()
            writer.writerows(output["equity"])
        manifest.update(status="SUCCESS", finished_at=datetime.now().astimezone().isoformat())
        atomic_json(directory / "manifest.json", manifest)
        research_report(directory / "report.html", manifest, summary, output["equity"])
        atomic_json(
            root / "experiment-index" / (rid + ".json"),
            {"run_id": rid, "status": "SUCCESS", "report": str(directory / "report.html")},
        )
        return {
            "run_id": rid,
            "status": "SUCCESS",
            "directory": str(directory),
            "snapshot_id": snapshot_id,
            "metrics": summary,
        }
    except Exception as error:
        manifest.update(
            status="INVALID",
            error={"code": getattr(error, "code", "ENGINE_ERROR"), "message": str(error)},
            finished_at=datetime.now().astimezone().isoformat(),
        )
        atomic_json(directory / "manifest.json", manifest)
        research_report(directory / "report.html", manifest, {"performance": "NOT_PUBLISHED"}, [])
        atomic_json(
            root / "experiment-index" / (rid + ".json"),
            {"run_id": rid, "status": "INVALID", "report": str(directory / "report.html")},
        )
        raise


def suite(root, config, snapshot_id, start, end, split="synthetic"):
    scenarios = [
        {
            "name": "base",
            "capital": config.initial_cash,
            "cost_multiplier": Decimal(1),
            "delay_days": 1,
            "slippage_bps": config.slippage_bps,
        },
        {
            "name": "cost_2x",
            "capital": config.initial_cash,
            "cost_multiplier": Decimal(2),
            "delay_days": 1,
            "slippage_bps": config.slippage_bps,
        },
        {
            "name": "cost_3x",
            "capital": config.initial_cash,
            "cost_multiplier": Decimal(3),
            "delay_days": 1,
            "slippage_bps": config.slippage_bps,
        },
        {
            "name": "delay_extra_day",
            "capital": config.initial_cash,
            "cost_multiplier": Decimal(1),
            "delay_days": 2,
            "slippage_bps": config.slippage_bps,
        },
        {
            "name": "capital_100k",
            "capital": Decimal(100000),
            "cost_multiplier": Decimal(1),
            "delay_days": 1,
            "slippage_bps": config.slippage_bps,
        },
        {
            "name": "capital_300k",
            "capital": Decimal(300000),
            "cost_multiplier": Decimal(1),
            "delay_days": 1,
            "slippage_bps": config.slippage_bps,
        },
        {
            "name": "adverse_10bps",
            "capital": config.initial_cash,
            "cost_multiplier": Decimal(1),
            "delay_days": 1,
            "slippage_bps": Decimal(10),
        },
    ]
    registration = {
        "snapshot_id": snapshot_id,
        "start": str(start),
        "end": str(end),
        "split": split,
        "scenarios": scenarios,
        "code": code_identity(),
        "parameters": config.model_dump(mode="json"),
        "optimization": False,
    }
    suite_id = stable_id(registration)
    directory = root / "suites" / suite_id
    # Fixed scenarios recorded before running any experiment. No parameter search.
    atomic_json(directory / "registration.json", registration)
    results = []
    for scenario in scenarios:
        scenario_config = config.model_copy(update={"slippage_bps": scenario["slippage_bps"]})
        result = run(
            root,
            scenario_config,
            snapshot_id,
            start,
            end,
            "combined",
            scenario["capital"],
            scenario["cost_multiplier"],
            scenario["delay_days"],
            split,
        )
        results.append({"name": scenario["name"], **result})
    for strategy in ("etf_trend", "stock_lowvol", "buy_hold", "stock_equal"):
        result = run(root, config, snapshot_id, start, end, strategy, split=split)
        results.append({"name": strategy, **result})
    atomic_json(directory / "results.json", results)
    research_report(
        directory / "report.html",
        {
            **registration,
            "suite_id": suite_id,
            "status": "SUCCESS",
            "scope_warning": "合成样本不能证明收益；真实数据权限和全市场覆盖单独验收",
        },
        results,
        [],
    )
    return {
        "suite_id": suite_id,
        "status": "SUCCESS",
        "experiments": len(results),
        "report": str(directory / "report.html"),
    }
