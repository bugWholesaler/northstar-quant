import html
import json
from datetime import datetime
from pathlib import Path

from ashare.data.snapshot import atomic_json
from ashare.domain.models import at

STYLE = """body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;background:#f5f7fb;color:#192435}h1{font-size:28px}section{background:white;border:1px solid #dce2ec;border-radius:10px;padding:20px;margin:18px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td,th{padding:9px;text-align:left;border-bottom:1px solid #eee}.badge{padding:8px;background:#eaf0ff;border-radius:5px}small{color:#526070}"""


def page(title, sections):
    return (
        '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + html.escape(title)
        + "</title><style>"
        + STYLE
        + "</style><h1>"
        + html.escape(title)
        + "</h1>"
        + "".join(
            "<section><h2>" + html.escape(name) + "</h2>" + content + "</section>"
            for name, content in sections
        )
        + "</html>"
    )


def pretty(value):
    return (
        "<pre>"
        + html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        + "</pre>"
    )


def status(ledger, day):
    job = ledger.db.execute(
        "SELECT state,body FROM jobs WHERE day=? ORDER BY rowid DESC LIMIT 1", (str(day),)
    ).fetchone()
    last = ledger.db.execute("SELECT day,state FROM jobs ORDER BY day DESC LIMIT 1").fetchone()
    return {
        "account_id": ledger.account_id,
        "mode": ledger.mode.value,
        "requested_date": str(day),
        "effective_date": last[0] if last else None,
        "job_status": job[0] if job else "TASK_MISSING",
        "data_status": json.loads(job[1]).get("data_status", "INCOMPLETE") if job else "STALE",
        "reconciliation": ledger.meta("reconciliation"),
        "risk_state": ledger.meta("risk_state"),
        "next_action": "查看阻断项，补齐数据/交割单并重新核对"
        if not job
        or ledger.meta("reconciliation") != "MATCHED"
        or ledger.meta("risk_state") != "RUNNING"
        else "查看未来计划与待办订单；无待办即可结束",
        "offsite_backup": "NOT_CONFIGURED",
    }


def daily(ledger, root: Path, day):
    state = status(ledger, day)
    account = ledger.view(at(day)).model_dump(mode="json")
    plans = [
        {
            "plan_id": r[0],
            "state": r[1],
            "valid_date": json.loads(r[2])["valid_date"],
            "blockers": json.loads(r[2])["blockers"],
        }
        for r in ledger.db.execute(
            "SELECT plan_id,state,body FROM plans ORDER BY rowid DESC LIMIT 10"
        )
    ]
    result = {
        **state,
        "account": account,
        "plans": plans,
        "orders": ledger.state()["orders"],
        "generated_at": datetime.now().astimezone().isoformat(),
        "limitations": [
            "合成样本仅验证工程流程"
            if ledger.mode.value == "demo"
            else "日频成交代理；真实投资前完成验收",
            "ETF 穿透暴露未知",
            "未配置离机备份",
            "未知公司行动使引擎运行 INVALID",
        ],
    }
    path = root / "report" / ledger.account_id / str(day)
    atomic_json(path / "daily.json", result)
    content = page(
        "Northstar · 每日报告",
        [
            ("今日状态与下一步", pretty(state)),
            ("账户 · 现金与持仓", pretty(account)),
            ("计划与阻断项", pretty(plans)),
            ("待办委托 · 导出不代表提交", pretty(result["orders"])),
            ("数据与能力边界", pretty(result["limitations"])),
        ],
    )
    (path / "daily.html").write_text(content)
    latest = root / "report" / "latest.html"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(content)
    atomic_json(root / "report" / "latest.json", result)
    return {"report": str(path / "daily.html"), "latest": str(latest), "status": state}


def research_report(path, manifest, metrics, equity):
    from ashare.reporting.charts import equity_chart

    chart = equity_chart(
        equity,
        path.parent,
        "SYNTHETIC engineering fixture"
        if manifest.get("snapshot", {}).get("scope") == "synthetic_sample"
        else "daily closing-price proxy",
    )
    path.write_text(
        page(
            "Northstar · 研究结果",
            [
                ("运行身份与验收状态", pretty(manifest)),
                ("指标 · 合成结果不能证明投资效果", pretty(metrics)),
                ("净资产与现金 · 可导出 PNG/SVG", chart),
                ("逐日净资产", pretty(equity)),
            ],
        )
    )
