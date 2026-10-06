"""Fixed free-data research trial. No parameter optimization or executable plans."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from ashare.data.snapshot import Store, atomic_json
from ashare.domain.errors import AshareError
from ashare.domain.models import Mode
from ashare.research.runner import code_identity, run


def run_trial(root: Path, config, snapshot_id, start: date, end: date):
    if config.mode != Mode.RESEARCH or config.backtest_profile != "research_scenario":
        raise AshareError("MODE_ISOLATION", "trial requires isolated research scenario")
    snapshot, _ = Store(root).load(snapshot_id)
    if snapshot.profile != "research_scenario":
        raise AshareError("SNAPSHOT_PROFILE", "wrong trial snapshot profile")
    cases = [
        ("combined", 1, 1, 200000, 5, ".2", "combined"),
        ("ETF", 1, 1, 200000, 5, ".2", "etf_trend"),
        ("stocks", 1, 1, 200000, 5, ".2", "stock_lowvol"),
        ("passive", 1, 1, 200000, 5, ".2", "combined_passive"),
        ("cost_2x", 2, 1, 200000, 5, ".2", "combined"),
        ("cost_3x", 3, 1, 200000, 5, ".2", "combined"),
        ("delay_2", 1, 2, 200000, 5, ".2", "combined"),
        ("slippage_10bps", 1, 1, 200000, 10, ".2", "combined"),
        ("capital_100k", 1, 1, 100000, 5, ".2", "combined"),
        ("capital_300k", 1, 1, 300000, 5, ".2", "combined"),
        ("dividend_tax_0", 1, 1, 200000, 5, "0", "combined"),
    ]
    registration = {
        "snapshot_id": snapshot_id,
        "start": str(start),
        "end": str(end),
        "cases": cases,
        "optimization": False,
        "code_identity": code_identity(),
        "sample": "current fixed 20 stocks + 3 ETFs; conditional retrospective study, not clean holdout",
    }
    atomic_json(root / "trial-registration.json", registration)
    results = []
    for name, cost, delay, cap, slip, tax, strategy in cases:
        conf = config.model_copy(
            update={"slippage_bps": Decimal(slip), "research_dividend_tax": Decimal(tax)}
        )
        result = run(
            root,
            conf,
            snapshot_id,
            start,
            end,
            strategy,
            Decimal(cap),
            Decimal(cost),
            delay,
            "development",
        )
        results.append({"name": name, **result})
        atomic_json(root / "trial-results.json", results)
    return render(root, results, registration)


def render(root: Path, results, registration):
    import html
    import io
    import json

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ashare.reporting.reports import page, pretty

    fig, axis = plt.subplots(figsize=(10, 4.2), layout="constrained")
    for row in results:
        if row["name"] not in ("combined", "ETF", "stocks", "passive"):
            continue
        equity = json.loads((Path(row["directory"]) / "equity.json").read_text())
        capital = Decimal(200000)
        axis.plot(
            [date.fromisoformat(x["date"]) for x in equity],
            [float(Decimal(x["nav"]) / capital) for x in equity],
            label=row["name"],
            linewidth=1.7,
        )
    axis.axhline(1, color="gray", linestyle="--", alpha=0.5, label="cash")
    axis.set_ylabel("Scenario NAV / initial capital")
    axis.set_title("Free-data conditional research | ETF dividend income incomplete")
    axis.grid(alpha=0.2)
    axis.legend(frameon=False, ncol=5)
    directory = root / "trial-report"
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / "comparison.png", dpi=180)
    fig.savefig(directory / "comparison.svg")
    stream = io.StringIO()
    fig.savefig(stream, format="svg")
    plt.close(fig)
    chart = stream.getvalue()
    chart = chart[chart.index("<svg") :]
    cells = []
    for row in results:
        m = row["metrics"]
        cells.append(
            "<tr>"
            + "".join(
                "<td>" + html.escape(str(v)) + "</td>"
                for v in (
                    row["name"],
                    f"{m['total_return']:.2%}",
                    f"{m['max_drawdown']:.2%}",
                    m["fees"],
                    m["fills"],
                    f"{m['mean_cash_weight']:.1%}",
                )
            )
            + "</tr>"
        )
    table = (
        "<table><thead><tr><th>情景</th><th>区间回报代理</th><th>最大回撤</th><th>交易费用 元</th><th>成交笔数</th><th>平均现金比例</th></tr></thead><tbody>"
        + "".join(cells)
        + "</tbody></table>"
    )
    notes = [
        f"{results[0]['metrics']['sessions']} 个交易日的条件样本，非全市场、非干净样本外，不据此推断长期收益",
        "组合：ETF最多40%+股票最多40%；单策略最多40%，风险预算与现金比例不同",
        "股票现金分红已按登记/除息/到账处理，固定20%预扣税情景不是实际持有期税务",
        "ETF信号使用后复权研究价；执行/估值用未复权价，ETF现金分红未完整计入，故称收益代理",
        "涨跌停参考价/上下限为登记规则情景，非每日官方核验；成交采用次日收盘代理+限价/量/滑点限制",
        "未知公司行动、送转、配股、退市仍阻断，真实账户与前瞻paper未开放",
        "被动基准采用同一三ETF与同一股票候选池，ETF40%股票40%，并非无风险或全市场指数",
        "额外一天延迟在冻结订单后执行；旧调度缺陷的零成交结果已标记无效保留，不用于结论",
    ]
    content = page(
        "Northstar · 免费真实数据首次回测",
        [
            (
                "结论",
                "<p>本次组合未呈现清晰超额。成本和延迟压力会进一步降低回报；模型缺口使实际完整净收益仍未知。</p>",
            ),
            ("净值比较 · 研究情景", chart),
            ("策略与固定压力情景", table),
            ("如何理解结果与未完成项", pretty(notes)),
            ("预登记参数和版本", pretty(registration)),
        ],
    )
    (directory / "report.html").write_text(content)
    atomic_json(
        directory / "summary.json",
        {
            "registration": registration,
            "results": [
                {"name": row["name"], "run_id": row["run_id"], "metrics": row["metrics"]}
                for row in results
            ],
            "notes": notes,
            "formal_investment_validation": "NOT_COMPLETE",
        },
    )
    return {
        "status": "SUCCESS",
        "report": str(directory / "report.html"),
        "summary": str(directory / "summary.json"),
        "experiments": len(results),
    }
