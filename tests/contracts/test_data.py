from datetime import date
from decimal import Decimal

import pytest

from ashare.adapters.data.tushare_provider import TushareProvider
from ashare.data.snapshot import MarketView, Store, publish, validate_tables
from ashare.domain.errors import AshareError
from ashare.domain.models import at


def test_point_in_time_view_and_future_shock(market, config):
    from ashare.strategies.rules import ETFTrend, StockLowVol

    root, snapshot, tables = market
    day = date(2025, 4, 30)
    view = MarketView(snapshot, tables, at(day))
    before = [ETFTrend(config).generate(view, day), StockLowVol(config).generate(view, day)]
    changed = {
        **tables,
        "bars": [
            b.model_copy(
                update={
                    "close": b.close * 10,
                    "open": b.open * 10,
                    "high": b.high * 10,
                    "low": b.low * 10,
                    "amount": b.amount * 10,
                }
            )
            if b.trade_date > day
            else b
            for b in tables["bars"]
        ],
    }
    after = [
        ETFTrend(config).generate(MarketView(snapshot, changed, at(day)), day),
        StockLowVol(config).generate(MarketView(snapshot, changed, at(day)), day),
    ]
    assert before == after
    assert all(b.trade_date <= day for b in view.bars("600000.SH", 500))
    assert len(before[0].weights) == 2 and len(before[1].weights) == 5


def test_later_delisted_instrument_not_removed_from_history(market, config):
    from ashare.strategies.rules import StockLowVol

    _, snapshot, tables = market
    changed = {
        **tables,
        "instruments": [
            i.model_copy(update={"delisted_on": date(2025, 5, 7)}) if i.symbol == "600000.SH" else i
            for i in tables["instruments"]
        ],
    }
    view = MarketView(snapshot, changed, at(date(2025, 4, 30)))
    assert "600000.SH" in view.instruments
    assert "600000.SH" in StockLowVol(config).generate(view, date(2025, 4, 30)).weights


def test_holiday_month_end_and_no_rebalance(market, config):
    from ashare.strategies.rules import ETFTrend

    _, snapshot, tables = market
    view = MarketView(snapshot, tables, at(date(2025, 4, 30)))
    assert view.is_month_end(date(2025, 4, 30))
    assert view.next_days(date(2025, 4, 30), 1) == [date(2025, 5, 2)]
    assert (
        ETFTrend(config)
        .generate(MarketView(snapshot, tables, at(date(2025, 4, 29))), date(2025, 4, 29))
        .status
        == "NO_REBALANCE"
    )


def test_short_window_is_cash_not_future_fill(market, config):
    from ashare.strategies.rules import ETFTrend

    _, snapshot, tables = market
    view = MarketView(snapshot, tables, at(date(2024, 1, 31)))
    assert ETFTrend(config).generate(view, date(2024, 1, 31)).weights == {}


def test_snapshot_content_hash_immutability_and_tamper(market):
    root, snapshot, tables = market
    again = publish(root, tables, "synthetic", "synthetic_sample", "historical_assumption")
    assert snapshot == again
    first = tables["bars"][0]
    replacement = first.model_copy(update={"close": first.close + Decimal("0.01")})
    changed = {**tables, "bars": [replacement] + tables["bars"][1:]}
    new = publish(root, changed, "synthetic", "synthetic_sample", "historical_assumption")
    assert new.snapshot_id != snapshot.snapshot_id
    assert (
        Store(root).load(snapshot.snapshot_id)[1]["bars"][0] != replacement or first == replacement
    )
    file = root / "snapshots" / snapshot.snapshot_id / snapshot.files["bars"]
    file.write_bytes(file.read_bytes() + b"broken")
    with pytest.raises(AshareError, match="integrity"):
        Store(root).load(snapshot.snapshot_id)


@pytest.mark.parametrize("change", ["missing", "duplicate", "units", "factor"])
def test_quality_gate_blocks_missing_conflicts_units_and_unexplained_factor(synthetic, change):
    tables = {**synthetic, "bars": list(synthetic["bars"])}
    if change == "missing":
        tables["bars"].pop(3)
    elif change == "duplicate":
        tables["bars"].append(tables["bars"][0])
    elif change == "units":
        tables["bars"][0] = tables["bars"][0].model_copy(update={"amount": Decimal("1")})
    else:
        tables["bars"][1] = tables["bars"][1].model_copy(update={"factor": Decimal(2)})
    with pytest.raises(AshareError):
        validate_tables(tables)


def test_provider_unit_conversion_equals_canonical(synthetic):
    canonical = synthetic["bars"][0]
    row = {
        "ts_code": canonical.instrument_id,
        "trade_date": canonical.trade_date.strftime("%Y%m%d"),
        "open": canonical.open,
        "high": canonical.high,
        "low": canonical.low,
        "close": canonical.close,
        "vol": Decimal(canonical.volume) / 100,
        "amount": canonical.amount / 1000,
    }
    status = {
        k: getattr(canonical, k)
        for k in ("suspended", "st", "delisting_period", "limit_up", "limit_down")
    }
    normalized = TushareProvider.normalize_bar(
        row,
        canonical.factor,
        status,
        canonical.available_at,
        canonical.observed_at,
        canonical.availability_basis,
    )
    assert normalized == canonical
    with pytest.raises(AshareError):
        TushareProvider.normalize_bar(
            row,
            canonical.factor,
            {},
            canonical.available_at,
            canonical.observed_at,
            canonical.availability_basis,
        )


def test_token_absent_never_claims_source_ready(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    report = TushareProvider().probe()
    assert not report["source_ready"]
    assert all(r["status"] == "NOT_TESTED" for r in report["datasets"].values())


def test_snapshot_atomic_publication_failure(tmp_path, synthetic, monkeypatch):
    import ashare.data.snapshot as module

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(module.pq, "write_table", fail)
    with pytest.raises(OSError):
        publish(tmp_path, synthetic, "synthetic", "synthetic_sample", "historical_assumption")
    assert not list((tmp_path / "snapshots").rglob("manifest.json"))
