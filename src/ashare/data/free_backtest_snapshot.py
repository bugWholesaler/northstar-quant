"""Explicit retrospective scenario inputs, never valid for live execution."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ashare.adapters.data.akshare_etf_provider import AKShareETFProvider
from ashare.adapters.data.baostock_provider import BaoStockProvider
from ashare.adapters.data.tencent_etf_provider import TencentETFProvider
from ashare.data.baostock_learning import unique_rows
from ashare.data.snapshot import atomic_json, file_hash, publish
from ashare.domain.errors import AshareError, Unsupported
from ashare.domain.models import (
    Bar,
    CalendarDay,
    CorporateAction,
    Instrument,
    Mode,
    at,
    decimal,
    stable_id,
)

RULE_SOURCES = [
    "https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml",
    "https://docs.static.szse.cn/www/aboutus/trends/news/W020200821764606607644.pdf",
]


def scenario_limits(reference, tick, asset, symbol, st, day):
    if reference <= 0:
        raise AshareError("REFERENCE_PRICE", "positive supplier reference price required")
    rate = Decimal("0.2") if symbol == "159915.SZ" else Decimal("0.1")
    if asset == "stock" and st and day < date(2026, 7, 6):
        rate = Decimal("0.05")
    from decimal import ROUND_HALF_UP

    return tuple(
        (reference * (1 + sign * rate) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
        for sign in (1, -1)
    )


def cash_actions(symbol, records, observed):
    result = {}
    for row in records:
        if not row.get("dividOperateDate"):
            raise Unsupported("dividend implementation/ex date not established")
        if decimal(row.get("dividStocksPs") or "0") or decimal(
            row.get("dividReserveToStockPs") or "0"
        ):
            raise Unsupported("bonus/share action not supported by first research snapshot")
        required = ["dividRegistDate", "dividOperateDate", "dividPayDate"]
        if any(not row.get(key) for key in required):
            raise Unsupported("cash action record/ex/pay dates incomplete")
        value = decimal(row.get("dividCashPsBeforeTax") or "0")
        if not value:
            continue
        announced = (
            row.get("dividPlanDate")
            or row.get("dividAgmPumDate")
            or row.get("dividPlanAnnounceDate")
        )
        if not announced:
            raise Unsupported("cash action publication date missing")
        payload = dict(
            instrument_id=symbol,
            kind="dividend",
            record_date=row["dividRegistDate"],
            ex_date=row["dividOperateDate"],
            pay_date=row["dividPayDate"],
            cash_per_share=value,
            published_at=at(date.fromisoformat(announced)),
            observed_at=observed,
            available_at=at(date.fromisoformat(announced)),
            implemented=True,
        )
        aid = stable_id({"symbol": symbol, "ex_date": payload["ex_date"], "cash": value})
        action = CorporateAction(action_id=aid, **payload)
        if aid in result:
            previous = result[aid]
            economic_fields = (
                "record_date",
                "ex_date",
                "pay_date",
                "cash_per_share",
                "bonus_ratio",
            )
            if any(getattr(previous, name) != getattr(action, name) for name in economic_fields):
                raise AshareError(
                    "ACTION_CONFLICT", "duplicate implemented action economic dates/amount disagree"
                )
            # Later final implementation notice supersedes the early proposal publication.
            action = action if action.published_at > previous.published_at else previous
        result[aid] = action
    return list(result.values())


def build(root: Path, config):
    if config.mode != Mode.RESEARCH or config.backtest_profile != "research_scenario":
        raise AshareError(
            "MODE_ISOLATION",
            "free-data snapshot builder is restricted to declared research scenario",
        )
    bao = BaoStockProvider(timeout=config.baostock.timeout)
    ak = (
        TencentETFProvider(network_route=config.research_etf_network_route)
        if config.research_etf_provider == "tencent"
        else AKShareETFProvider(network_route=config.research_etf_network_route)
    )
    inputs, tables = {}, {"instruments": [], "calendar": [], "bars": [], "actions": []}

    def get(name, dataset, params):
        result = bao.fetch(root, dataset, params)
        path = Path(result["directory"]) / "response.json"
        inputs[name] = {
            "provider": "baostock",
            "file": str(path),
            "hash": file_hash(path),
            "observed_at": result["observed_at"],
        }
        return result

    cal = get(
        "calendar",
        "calendar",
        {"start": str(config.baostock.start), "end": str(config.baostock.end + timedelta(days=30))},
    )
    calendar_rows = unique_rows(cal["rows"], "calendar_date")
    for day, row in sorted(calendar_rows.items()):
        tables["calendar"].extend(
            CalendarDay(exchange=e, trade_date=day, is_open=row["is_trading_day"] == "1")
            for e in ("XSHG", "XSHE")
        )
    for symbol in config.baostock.symbols:
        asset = "etf" if symbol in config.etf_universe else "stock"
        basic = get(symbol + ":basic", "basic", {"symbol": symbol})["rows"]
        if len(basic) != 1 or basic[0]["type"] != ("5" if asset == "etf" else "1"):
            raise AshareError(
                "INSTRUMENT", "source asset mapping missing or disagrees", symbol=symbol
            )
        inst = Instrument(
            instrument_id=symbol,
            symbol=symbol,
            exchange="XSHG" if symbol.endswith("SH") else "XSHE",
            asset_type=asset,
            name=basic[0]["code_name"],
            board="EQUITY_ETF" if asset == "etf" else "MAIN",
            listed_on=basic[0]["ipoDate"],
            delisted_on=basic[0]["outDate"] or None,
            tick=Decimal("0.001") if asset == "etf" else Decimal("0.01"),
        )
        tables["instruments"].append(inst)
        if asset == "stock":
            common = {
                "symbol": symbol,
                "start": str(config.baostock.start),
                "end": str(config.baostock.end),
                "asset_type": asset,
            }
            raw = get(symbol + ":raw", "daily", {**common, "adjustflag": "3"})
            adjusted = get(symbol + ":adjusted", "daily", {**common, "adjustflag": "1"})
            r = unique_rows(raw["rows"], "date")
            a = unique_rows(adjusted["rows"], "date")
            records = []
            for year in range(config.baostock.start.year, config.baostock.end.year + 1):
                records += get(
                    symbol + ":dividend:" + str(year),
                    "dividend",
                    {"symbol": symbol, "year": str(year)},
                )["rows"]
            observed = raw["observed_at"]
            tables["actions"] += cash_actions(symbol, records, observed)
            if set(r) != set(a):
                raise AshareError("HISTORY_INCOMPLETE", "raw/adjusted dates differ", symbol=symbol)
            normalized = []
            for day, row in sorted(r.items()):
                if (
                    row["adjustflag"] != "3"
                    or a[day]["adjustflag"] != "1"
                    or row.get("isST") not in ("0", "1")
                    or row["tradestatus"] not in ("0", "1")
                ):
                    raise AshareError("SOURCE_STATUS", "source adjustment/trading status mismatch")
                normalized.append(
                    dict(
                        day=day,
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=int(decimal(row["volume"] or "0")),
                        amount=row["amount"] or "0",
                        reference=row["preclose"],
                        research_close=a[day]["close"],
                        st=row["isST"] == "1",
                        suspended=row["tradestatus"] == "0",
                    )
                )
        else:
            raw = ak.fetch(root, symbol, config.baostock.start, config.baostock.end, "")
            adjusted = ak.fetch(root, symbol, config.baostock.start, config.baostock.end, "hfq")
            for name, response in (("raw", raw), ("adjusted", adjusted)):
                inputs[symbol + ":" + name] = {
                    "provider": config.research_etf_provider,
                    "file": response["file"],
                    "hash": file_hash(Path(response["file"])),
                    "observed_at": response["observed_at"],
                }
            r = unique_rows(raw["rows"], "日期")
            a = unique_rows(adjusted["rows"], "日期")
            if set(r) != set(a):
                raise AshareError("HISTORY_INCOMPLETE", "ETF raw/adjusted dates differ")
            observed = raw["observed_at"]
            normalized = []
            previous_adjusted = None
            for day, row in sorted(r.items()):
                volume = decimal(row["成交量"]) * 100
                if volume != int(volume):
                    raise AshareError("VOLUME_UNIT", "ETF volume conversion nonintegral")
                research_close = decimal(a[day]["收盘"])
                if config.research_etf_provider == "tencent":
                    reference = (
                        (previous_adjusted * decimal(row["收盘"]) / research_close)
                        if previous_adjusted is not None
                        else decimal(row["开盘"])
                    )
                else:
                    reference = decimal(row["收盘"]) - decimal(row["涨跌额"])
                previous_adjusted = research_close
                normalized.append(
                    dict(
                        day=str(day)[:10],
                        open=row["开盘"],
                        high=row["最高"],
                        low=row["最低"],
                        close=row["收盘"],
                        volume=int(volume),
                        amount=row["成交额"],
                        reference=reference,
                        research_close=a[day]["收盘"],
                        st=False,
                        suspended=volume == 0,
                    )
                )
        for row in normalized:
            day = date.fromisoformat(row.pop("day"))
            up, down = scenario_limits(
                decimal(row.pop("reference")), inst.tick, asset, symbol, row["st"], day
            )
            tables["bars"].append(
                Bar(
                    instrument_id=symbol,
                    trade_date=day,
                    **row,
                    factor=Decimal(1),
                    adjustment_basis="vendor_research_close",
                    limit_up=up,
                    limit_down=down,
                    available_at=at(day),
                    observed_at=observed,
                    availability_basis="historical_assumption",
                )
            )
    provenance = {
        "route": {"stocks": "baostock", "ETF": config.research_etf_provider},
        "inputs": inputs,
        "rule_sources": RULE_SOURCES,
        "assumptions": [
            "固定当前20股/3ETF的条件样本，非全市场；有选择及幸存者偏差",
            "非商业研究情景；不能直接用于 paper/manual",
            "涨跌停价根据参考价及登记的交易所规则推导，未逐日独立验证；腾讯ETF参考价按前日后复权价转换为当日价格基准",
            "信号使用供应商后复权价格的本次版本；历史可用时间推定，非严格PIT",
            "股票实施现金分红按明确固定20%预扣税情景；不是实际批次持有期税务模型",
            "ETF成交/估值用未复权价格，ETF现金分红尚未完整接入；ETF及组合结果是未完整计入ETF股利的回报代理",
            "送转/配股/清算/退市仍不支持，出现则INVALID；无真实券商费率及实时排队模型",
        ],
    }
    snapshot = publish(
        root,
        tables,
        "baostock+" + config.research_etf_provider,
        "real_sample",
        "historical_assumption",
        provenance,
        profile="research_scenario",
    )
    atomic_json(
        root / "free-backtest-input.json",
        {
            "snapshot_id": snapshot.snapshot_id,
            "profile": snapshot.profile,
            "scope": snapshot.scope,
            "assumptions": provenance["assumptions"],
            "rows": snapshot.quality["rows"],
        },
    )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "profile": snapshot.profile,
        "rows": snapshot.quality["rows"],
        "trading_ready": False,
    }
