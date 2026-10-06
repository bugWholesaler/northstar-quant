import subprocess
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ashare.adapters.data.baostock_provider import (
    BaoStockProvider,
    request_parameters,
    vendor_symbol,
)
from ashare.adapters.data.baostock_worker import collect
from ashare.config import BaoStockSettings, Config
from ashare.data.baostock_learning import research_view
from ashare.domain.errors import AshareError
from ashare.domain.models import Mode


def response(dataset, params):
    return {"status": "SUCCESS", "fields": ["value"], "rows": [{"value": "fixture"}]}


def test_baostock_symbol_mapping_and_units_are_not_tushare_units():
    assert vendor_symbol("600000.SH") == "sh.600000"
    assert vendor_symbol("159915.SZ") == "sz.159915"
    assert (
        request_parameters(
            "daily",
            {
                "symbol": "510300.SH",
                "start": "2026-09-21",
                "end": "2026-09-30",
                "asset_type": "etf",
            },
        )["adjustflag"]
        == "3"
    )
    with pytest.raises(AshareError):
        vendor_symbol("600000")


@pytest.mark.parametrize(
    "parameters",
    [
        {"symbol": "600000.SH", "token": "secret"},
        {"symbol": "600000.SH", "password": "secret"},
        {"symbol": "600000.SH", "api_key": "secret"},
    ],
)
def test_baostock_rejects_secret_parameters(parameters):
    with pytest.raises(AshareError):
        request_parameters("basic", parameters)


def test_baostock_raw_cache_and_refresh_are_immutable(tmp_path):
    calls = []

    def transport(dataset, params):
        calls.append((dataset, params))
        return response(dataset, params)

    provider = BaoStockProvider(transport=transport)
    first = provider.fetch(tmp_path, "basic", {"symbol": "600000.SH"})
    second = provider.fetch(tmp_path, "basic", {"symbol": "600000.SH"})
    assert len(calls) == 1 and second["cached"] and second["observed_at"] == first["observed_at"]
    fresh = provider.fetch(tmp_path, "basic", {"symbol": "600000.SH"}, refresh=True)
    assert len(calls) == 2 and fresh["directory"] != first["directory"]
    from pathlib import Path

    (Path(first["directory"]) / "response.json").write_text("corrupted")
    with pytest.raises(AshareError, match="modified"):
        provider.fetch(tmp_path, "basic", {"symbol": "600000.SH"})


def test_baostock_timeout_does_not_fallback(tmp_path, monkeypatch):
    import ashare.adapters.data.baostock_provider as module

    monkeypatch.setattr(module, "version", lambda package: "0.9.4")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture", 1)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(AshareError) as error:
        BaoStockProvider(timeout=1).fetch(tmp_path, "basic", {"symbol": "600000.SH"})
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert list(tmp_path.rglob("failure.json")) and not list(tmp_path.rglob("response.json"))


def test_baostock_partial_page_failure_is_not_success():
    class BrokenPage:
        fields = ["date"]
        error_code = "0"
        cur_page_num = "1"
        per_page_count = "2"
        data = [1, 2]

        def next(self):
            return False

    with pytest.raises(ValueError, match="PAGINATION_INCOMPLETE"):
        collect(BrokenPage())


def test_baostock_query_error_is_not_empty_dataset():
    with pytest.raises(ValueError, match="QUERY_FAILED"):
        collect(SimpleNamespace(error_code="100", fields=[]))


def test_baostock_current_raw_fields_do_not_claim_tradable_source(tmp_path):
    report = BaoStockProvider(transport=response).probe(tmp_path)
    assert not report["source_ready"]
    assert "verified_daily_price_limits" in report["missing"]


def test_baostock_learning_bundle_rejects_paper_and_manual(tmp_path):
    for mode in (Mode.PAPER, Mode.MANUAL):
        cfg = Config(mode=mode, account_id=mode.value, provider="baostock", availability="observed")
        with pytest.raises(AshareError, match="research/real_sample"):
            BaoStockProvider(transport=response).sync(cfg, tmp_path)


def learning_inputs():
    cfg = Config(
        mode=Mode.RESEARCH,
        account_id="research",
        provider="baostock",
        baostock=BaoStockSettings(
            start=date(2026, 9, 29), end=date(2026, 9, 30), symbols=["600000.SH"]
        ),
    )
    end = date(2026, 10, 30)
    from datetime import timedelta

    calendar = []
    day = cfg.baostock.start
    while day <= end:
        calendar.append(
            {"calendar_date": str(day), "is_trading_day": "1" if day.weekday() < 5 else "0"}
        )
        day += timedelta(days=1)
    rows = [
        {
            "date": str(day),
            "code": "sh.600000",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10",
            "preclose": "10",
            "volume": "1000",
            "amount": "10000",
            "adjustflag": "3",
            "tradestatus": "1",
            "isST": "0",
        }
        for day in (cfg.baostock.start, cfg.baostock.end)
    ]
    responses = {
        "calendar": {"rows": calendar},
        "600000.SH:basic": {
            "rows": [
                {
                    "code": "sh.600000",
                    "code_name": "fixture",
                    "type": "1",
                    "ipoDate": "2000-01-01",
                    "outDate": "",
                }
            ]
        },
        "600000.SH:raw": {"rows": rows},
        "600000.SH:adjusted": {"rows": [{**row, "adjustflag": "1", "close": "20"} for row in rows]},
    }
    return cfg, {"calendar_end": end}, responses


def test_learning_normalizes_share_volume_and_adjusted_price():
    cfg, bundle, responses = learning_inputs()
    view = research_view("fixture", bundle, responses, cfg)
    bar = view.bars("600000.SH", 1)[0]
    assert bar.volume == 1000 and bar.amount == Decimal(10000)
    assert bar.close * bar.factor == 20
    assert not hasattr(bar, "limit_up")  # cannot impersonate a canonical executable bar


@pytest.mark.parametrize(
    "fault", ["missing_bar", "duplicate", "unit", "missing_st", "calendar", "adjustment"]
)
def test_learning_rejects_missing_or_invalid_source_data(fault):
    cfg, bundle, responses = learning_inputs()
    if fault == "missing_bar":
        responses["600000.SH:raw"]["rows"].pop()
    elif fault == "duplicate":
        responses["600000.SH:raw"]["rows"].append(responses["600000.SH:raw"]["rows"][0])
    elif fault == "unit":
        responses["600000.SH:raw"]["rows"][0]["amount"] = "1"
    elif fault == "missing_st":
        responses["600000.SH:raw"]["rows"][0]["isST"] = ""
    elif fault == "calendar":
        responses["calendar"]["rows"].pop()
    else:
        responses["600000.SH:adjusted"]["rows"][0]["adjustflag"] = "3"
    with pytest.raises(AshareError):
        research_view("fixture", bundle, responses, cfg)


def etf_inputs():
    cfg, bundle, responses = learning_inputs()
    cfg = cfg.model_copy(
        update={"baostock": cfg.baostock.model_copy(update={"symbols": ["510300.SH"]})}
    )
    result = {"calendar": responses["calendar"]}
    result["510300.SH:basic"] = {
        "rows": [
            {
                "code": "sh.510300",
                "code_name": "fixture ETF",
                "type": "5",
                "ipoDate": "2012-05-28",
                "outDate": "",
            }
        ]
    }
    rows = [{**row, "code": "sh.510300"} for row in responses["600000.SH:raw"]["rows"]]
    result["510300.SH:raw"] = {"rows": rows}
    result["510300.SH:adjusted"] = {"rows": rows}  # provider ignored requested adjustment
    return cfg, bundle, result


def test_etf_ignored_adjustment_is_rejected_in_verified_mode():
    cfg, bundle, responses = etf_inputs()
    with pytest.raises(AshareError, match="mapping mismatch"):
        research_view("fixture", bundle, responses, cfg)


def test_explicit_unadjusted_etf_proxy_is_not_a_total_return_factor():
    cfg, bundle, responses = etf_inputs()
    cfg = cfg.model_copy(
        update={"baostock": cfg.baostock.model_copy(update={"etf_price_mode": "unadjusted_proxy"})}
    )
    responses.pop("510300.SH:adjusted")
    view = research_view("fixture", bundle, responses, cfg)
    assert view.bars("510300.SH", 1)[0].factor == 1


def test_learning_sync_creates_no_trading_snapshot_or_ledger(tmp_path):
    cfg, bundle, responses = learning_inputs()

    def transport(dataset, params):
        if dataset == "calendar":
            rows = responses["calendar"]["rows"]
        elif dataset == "basic":
            rows = responses["600000.SH:basic"]["rows"]
        elif dataset == "daily":
            rows = responses["600000.SH:" + ("raw" if params["adjustflag"] == "3" else "adjusted")][
                "rows"
            ]
        else:
            rows = []
        return {"status": "SUCCESS", "fields": list(rows[0]) if rows else [], "rows": rows}

    result = BaoStockProvider(transport=transport).sync(cfg, tmp_path)
    assert result["status"] == "RESEARCH_PREVIEW_READY" and not result["executable"]
    assert not (tmp_path / "snapshots").exists() and not (tmp_path / "state").exists()


def test_optional_baostock_settings_reject_date_and_duplicate_errors():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BaoStockSettings(start=date(2026, 9, 30), end=date(2026, 9, 29))
    with pytest.raises(ValidationError):
        BaoStockSettings(symbols=["600000.SH", "600000.SH"])
    with pytest.raises(ValidationError):
        BaoStockSettings(etf_start=date(2027, 1, 1))


def test_same_raw_request_cannot_have_two_writers(tmp_path):
    from ashare.ops.locks import account_lock

    provider = BaoStockProvider(transport=response)
    first = provider.fetch(tmp_path, "basic", {"symbol": "600000.SH"})
    with account_lock(tmp_path, "data-baostock-" + first["request_id"]):
        with pytest.raises(AshareError) as error:
            provider.fetch(tmp_path, "basic", {"symbol": "600000.SH"})
        assert error.value.code == "LOCK_CONFLICT"
