"""BaoStock research signal preview; deliberately has no account or engine adapter."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from ashare.adapters.data.baostock_provider import vendor_symbol
from ashare.data.snapshot import MarketView, atomic_json, file_hash
from ashare.domain.errors import AshareError
from ashare.domain.models import CalendarDay, Instrument, Mode, at, decimal, stable_id
from ashare.reporting.reports import page, pretty
from ashare.strategies.rules import ETFTrend, StockLowVol

ASSUMPTIONS = [
    "固定当前样本，不能代表历史全市场选股；不消除幸存者/样本选择偏差",
    "日线与供应商后复权序列为本次获取的版本，历史可用时间按盘后推定，不是严格 PIT",
    "BaoStock 的公共 A 股日历映射至沪深两个交易所，尚未分别与交易所日历交叉核验",
    "BaoStock tradestatus/isST 已读取；历史退市整理期字段缺失，信号预览采用当前在市样本的简化状态",
    "每日实际涨跌停及 ETF 公司行动完整性未核验，禁止生成可执行清单或发布成交绩效",
]


def unique_rows(rows, key):
    result = {}
    for row in rows:
        value = row[key]
        if value in result:
            raise AshareError("PROVIDER_DUPLICATE", "duplicate source primary key", key=value)
        result[value] = row
    return result


def research_view(bundle_id, bundle, responses, config):
    instruments, bars = [], []
    raw_calendar = responses["calendar"]["rows"]
    days = unique_rows(raw_calendar, "calendar_date")
    expected_dates = [
        config.baostock.start + timedelta(days=n)
        for n in range((bundle["calendar_end"] - config.baostock.start).days + 1)
    ]
    if set(days) != {str(d) for d in expected_dates}:
        raise AshareError("CALENDAR_INCOMPLETE", "provider calendar has missing/extra dates")
    calendar = []
    for day in expected_dates:
        status = days[str(day)]["is_trading_day"]
        if status not in ("0", "1"):
            raise AshareError("CALENDAR_STATE", "unknown exchange calendar status")
        calendar.extend(
            CalendarDay(exchange=exchange, trade_date=day, is_open=status == "1")
            for exchange in ("XSHG", "XSHE")
        )
    for symbol in config.baostock.symbols:
        asset = "etf" if symbol in config.etf_universe else "stock"
        basic = responses[symbol + ":basic"]["rows"]
        if len(basic) != 1 or basic[0]["code"] != vendor_symbol(symbol):
            raise AshareError(
                "INSTRUMENT_MISSING",
                "provider instrument must uniquely match requested symbol",
                symbol=symbol,
            )
        metadata = basic[0]
        if metadata["type"] != ("5" if asset == "etf" else "1"):
            raise AshareError(
                "ASSET_TYPE",
                "provider asset type disagrees with registered universe",
                symbol=symbol,
            )
        inst = Instrument(
            instrument_id=symbol,
            symbol=symbol,
            exchange="XSHG" if symbol.endswith("SH") else "XSHE",
            asset_type=asset,
            name=metadata["code_name"],
            board="EQUITY_ETF" if asset == "etf" else "MAIN",
            listed_on=metadata["ipoDate"],
            delisted_on=metadata.get("outDate") or None,
            tick=Decimal("0.001") if asset == "etf" else Decimal("0.01"),
        )
        instruments.append(inst)
        raw = unique_rows(responses[symbol + ":raw"]["rows"], "date")
        proxy_etf = asset == "etf" and config.baostock.etf_price_mode == "unadjusted_proxy"
        adjusted = (
            raw if proxy_etf else unique_rows(responses[symbol + ":adjusted"]["rows"], "date")
        )
        asset_start = (
            config.baostock.etf_start or config.baostock.start
            if asset == "etf"
            else config.baostock.start
        )
        expected = {
            str(c.trade_date)
            for c in calendar
            if c.exchange == inst.exchange
            and c.is_open
            and max(inst.listed_on, asset_start) <= c.trade_date <= config.baostock.end
            and (not inst.delisted_on or c.trade_date < inst.delisted_on)
        }
        if set(raw) != expected or set(adjusted) != expected:
            raise AshareError(
                "HISTORY_INCOMPLETE",
                "raw/adjusted bars do not cover expected listing/calendar dates",
                symbol=symbol,
                raw_missing=sorted(expected - set(raw))[:10],
                adjusted_missing=sorted(expected - set(adjusted))[:10],
            )
        for day in sorted(raw):
            row, adj = raw[day], adjusted[day]
            if (
                row["code"] != vendor_symbol(symbol)
                or adj["code"] != row["code"]
                or row["adjustflag"] != "3"
                or adj["adjustflag"] != ("3" if proxy_etf else "1")
            ):
                raise AshareError(
                    "ADJUSTMENT_MAPPING",
                    "raw/adjusted source mapping mismatch",
                    symbol=symbol,
                    day=day,
                )
            if row["tradestatus"] not in ("0", "1") or (
                asset == "stock" and row.get("isST") not in ("0", "1")
            ):
                raise AshareError(
                    "STATUS_MISSING", "source trading/ST status is unknown", symbol=symbol, day=day
                )
            suspended = row["tradestatus"] == "0"
            close, research_close = decimal(row["close"]), decimal(adj["close"])
            prices = {field: decimal(row[field]) for field in ("open", "high", "low", "close")}
            volume, amount = decimal(row["volume"] or "0"), decimal(row["amount"] or "0")
            if (
                close <= 0
                or research_close <= 0
                or volume < 0
                or volume != int(volume)
                or amount < 0
            ):
                raise AshareError(
                    "BAR_VALUE", "invalid source price/quantity/amount", symbol=symbol, day=day
                )
            if (
                not prices["low"]
                <= min(prices["open"], close)
                <= max(prices["open"], close)
                <= prices["high"]
            ):
                raise AshareError("OHLC", "source OHLC range invalid", symbol=symbol, day=day)
            if not suspended and (
                not volume
                or not amount
                or not prices["low"] * Decimal("0.8")
                <= amount / volume
                <= prices["high"] * Decimal("1.2")
            ):
                raise AshareError(
                    "VOLUME_AMOUNT_UNIT",
                    "source volume/amount units inconsistent",
                    symbol=symbol,
                    day=day,
                )
            bars.append(
                SimpleNamespace(
                    instrument_id=symbol,
                    trade_date=date.fromisoformat(day),
                    close=close,
                    factor=research_close / close,
                    amount=amount,
                    volume=int(volume),
                    suspended=suspended,
                    st=row.get("isST") == "1",
                    delisting_period=False,
                    available_at=at(date.fromisoformat(day)),
                )
            )
    snapshot_ref = SimpleNamespace(snapshot_id=bundle_id, scope="real_sample")
    return MarketView(
        snapshot_ref,
        {"instruments": instruments, "calendar": calendar, "bars": bars, "actions": []},
        at(config.baostock.end),
    )


def fetch_bundle(provider, root: Path, config):
    if (
        config.mode != Mode.RESEARCH
        or config.scope != "real_sample"
        or config.availability != "historical_assumption"
    ):
        raise AshareError(
            "MODE_ISOLATION",
            "BaoStock learning bundle requires research/real_sample/historical_assumption",
        )
    if not config.baostock.symbols or len(config.baostock.symbols) > 100:
        raise AshareError("SAMPLE_SIZE", "register 1..100 sample symbols for first free-data trial")
    responses, inputs = {}, {}
    calendar_end = config.baostock.end + timedelta(days=30)

    def get(name, dataset, params):
        result = provider.fetch(root, dataset, params, config.baostock.refresh)
        responses[name] = result
        inputs[name] = {
            "request_id": result["request_id"],
            "file": str(Path(result["directory"]) / "response.json"),
            "sha256": file_hash(Path(result["directory"]) / "response.json"),
            "observed_at": result["observed_at"],
            "rows": len(result["rows"]),
        }

    get("calendar", "calendar", {"start": str(config.baostock.start), "end": str(calendar_end)})
    for symbol in config.baostock.symbols:
        asset = "etf" if symbol in config.etf_universe else "stock"
        get(symbol + ":basic", "basic", {"symbol": symbol})
        common = {
            "symbol": symbol,
            "start": str(
                (config.baostock.etf_start or config.baostock.start)
                if asset == "etf"
                else config.baostock.start
            ),
            "end": str(config.baostock.end),
            "asset_type": asset,
        }
        get(symbol + ":raw", "daily", {**common, "adjustflag": "3"})
        if asset != "etf" or config.baostock.etf_price_mode != "unadjusted_proxy":
            get(symbol + ":adjusted", "daily", {**common, "adjustflag": "1"})
        get(
            symbol + ":factor",
            "adjust_factor",
            {
                "symbol": symbol,
                "start": str(config.baostock.start),
                "end": str(config.baostock.end),
            },
        )
        for year in range(config.baostock.start.year, config.baostock.end.year + 1):
            get(
                symbol + ":dividend:" + str(year), "dividend", {"symbol": symbol, "year": str(year)}
            )
    assumptions = list(ASSUMPTIONS)
    if config.baostock.etf_price_mode == "unadjusted_proxy":
        assumptions.append(
            "ETF 明确使用未复权价格代理，分红/拆分未计，不是原版复权 ETF 策略；ETF 历史起点单独登记"
        )
    from ashare.research.runner import code_identity

    code = code_identity()
    identity = {
        "implementation": {"source_hash": code["source_hash"], "lock_hash": code["lock_hash"]},
        "inputs": inputs,
        "config": config.model_dump(mode="json", exclude={"runtime"}),
        "transform": "baostock-research-preview-1",
    }
    bid = stable_id(identity)
    directory = root / "learning" / bid
    manifest = {
        "schema_version": "1.0",
        "bundle_id": bid,
        "provider": "baostock",
        "sdk_version": "0.9.4",
        "code_identity": code,
        "scope": "real_sample",
        "profile": "SIGNAL_ONLY",
        "executable": False,
        "snapshot_status": "NOT_PUBLISHED",
        "availability": "historical_assumption",
        "calendar_end": calendar_end,
        "assumptions": assumptions,
        **identity,
    }
    try:
        view = research_view(bid, manifest, responses, config)
        current = sorted({bar.trade_date for rows in view._bars.values() for bar in rows})[-1]
        if current != config.baostock.end:
            raise AshareError(
                "END_DATE",
                "registered end date must have complete trading-day bars",
                requested=str(config.baostock.end),
                latest=str(current),
            )
        strategies = [ETFTrend(config), StockLowVol(config)]
        targets = [
            strategy.generate(view, current).model_dump(mode="json") for strategy in strategies
        ]
        if config.baostock.etf_price_mode == "unadjusted_proxy":
            targets[0].update(
                strategy_id="etf_trend_price_proxy", strategy_version="learning-proxy-1"
            )
            targets[0]["reasons"] = {
                symbol: reason.replace("adjusted momentum", "unadjusted price momentum proxy")
                for symbol, reason in targets[0]["reasons"].items()
            }
            targets[0]["target_id"] = stable_id(
                {k: v for k, v in targets[0].items() if k != "target_id"}
            )
        preview = {
            "bundle_id": bid,
            "asof": str(current),
            "executable": False,
            "profile": "SIGNAL_ONLY",
            "price_modes": {
                "stock": "vendor_backward_adjusted",
                "etf": config.baostock.etf_price_mode,
            },
            "targets": targets,
            "coverage": {
                "instruments": len(view.instruments),
                "bars": sum(len(rows) for rows in view._bars.values()),
            },
            "assumptions": assumptions,
        }
        manifest["status"] = "RESEARCH_PREVIEW_READY"
        atomic_json(directory / "manifest.json", manifest)
        atomic_json(directory / "signals.json", preview)
        (directory / "report.html").write_text(
            page(
                "Northstar · BaoStock 免费真实行情研究预览",
                [
                    (
                        "真实小样本 · 无可执行清单",
                        pretty(
                            {
                                "asof": str(current),
                                "profile": "SIGNAL_ONLY",
                                "executable": False,
                                "coverage": preview["coverage"],
                                "price_modes": preview["price_modes"],
                            }
                        ),
                    ),
                    ("ETF 与主板股票信号", pretty(targets)),
                    ("数据假设与执行缺口", pretty(assumptions)),
                ],
            )
        )
        atomic_json(
            root / "learning-latest.json",
            {
                "bundle_id": bid,
                "report": str(directory / "report.html"),
                "signals": str(directory / "signals.json"),
            },
        )
        return {
            "bundle_id": bid,
            "status": "RESEARCH_PREVIEW_READY",
            "scope": "real_sample",
            "executable": False,
            "snapshot_status": "NOT_PUBLISHED",
            "report": str(directory / "report.html"),
            "signals": str(directory / "signals.json"),
            "coverage": preview["coverage"],
        }
    except Exception as error:
        manifest.update(
            status="INVALID", error={"code": getattr(error, "code", "INPUT"), "message": str(error)}
        )
        atomic_json(directory / "manifest.json", manifest)
        raise
