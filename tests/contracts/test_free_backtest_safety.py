from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ashare.adapters.data.tencent_etf_provider import TencentETFProvider
from ashare.domain.errors import AshareError


def test_tencent_filtered_range_and_exact_amount_units(tmp_path, monkeypatch):
    import ashare.adapters.data.tencent_etf_provider as module

    point = [
        "2026-09-30",
        "7.498",
        "7.472",
        "7.513",
        "7.450",
        "2232233.000",
        {},
        "1",
        "166778.1234",
    ]
    body = {"data": {"sh510500": {"day": [["2026-09-29", *point[1:]], point]}}}
    response = SimpleNamespace(text=__import__("json").dumps(body), raise_for_status=lambda: None)

    class Session:
        trust_env = True

        def get(self, *args, **kwargs):
            return response

        def close(self):
            pass

    monkeypatch.setattr(module.requests, "Session", Session)
    result = TencentETFProvider().fetch(
        tmp_path, "510500.SH", date(2026, 9, 30), date(2026, 9, 30), ""
    )
    assert len(result["rows"]) == 1
    assert Decimal(result["rows"][0]["成交额"]) == Decimal("1667781234")
    assert result["rows"][0]["成交量"] == "2232233.000"
    assert (
        TencentETFProvider().fetch(tmp_path, "510500.SH", date(2026, 9, 30), date(2026, 9, 30), "")[
            "request_id"
        ]
        == result["request_id"]
    )


def test_tencent_does_not_substitute_unadjusted_when_hfq_missing(tmp_path, monkeypatch):
    import ashare.adapters.data.tencent_etf_provider as module

    body = {
        "data": {"sh510500": {"day": [["2026-09-30", "7", "7", "7", "7", "100", {}, "1", "100"]]}}
    }

    class Session:
        trust_env = True

        def get(self, *args, **kwargs):
            return SimpleNamespace(
                text=__import__("json").dumps(body), raise_for_status=lambda: None
            )

        def close(self):
            pass

    monkeypatch.setattr(module.requests, "Session", Session)
    with pytest.raises(AshareError, match="ignored adjustment"):
        TencentETFProvider().fetch(
            tmp_path, "510500.SH", date(2026, 9, 30), date(2026, 9, 30), "hfq"
        )
