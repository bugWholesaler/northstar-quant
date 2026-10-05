"""Exportable scientific plots; no network or account mutation."""

from datetime import date
from io import StringIO
from pathlib import Path


def equity_chart(equity, directory: Path, label="daily proxy"):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    if not equity:
        return ""
    days = [date.fromisoformat(row["date"]) for row in equity]
    values = [float(row["nav"]) for row in equity]
    cash = [float(row["cash"]) for row in equity]
    figure, axis = plt.subplots(figsize=(9, 3.6), layout="constrained")
    axis.plot(days, values, label="Net assets after trading costs", color="#2359bd", linewidth=1.8)
    axis.plot(days, cash, label="Cash", color="#64748b", linewidth=1.1, alpha=0.85)
    axis.set_title("Northstar | " + label)
    axis.set_ylabel("CNY")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, position: f"{value:,.0f}"))
    axis.grid(alpha=0.18)
    axis.legend(loc="best", frameon=False, fontsize=8)
    directory.mkdir(parents=True, exist_ok=True)
    figure.savefig(directory / "equity.png", dpi=180)
    figure.savefig(directory / "equity.svg")
    stream = StringIO()
    figure.savefig(stream, format="svg")
    plt.close(figure)
    content = stream.getvalue()
    return content[content.index("<svg") :]
