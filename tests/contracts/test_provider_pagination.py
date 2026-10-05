import pandas as pd
import pytest

from ashare.adapters.data.tushare_provider import TushareProvider
from ashare.domain.errors import AshareError


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.repeat = False

    def query(self, dataset, **params):
        offset = params["offset"]
        self.calls.append(offset)
        if self.fail and offset == 2:
            raise RuntimeError("SDK error including fake secret: NEVER_PRINT")
        rows = [{"id": 0}, {"id": 1}, {"id": 2}]
        return pd.DataFrame(rows[:2] if self.repeat else rows[offset : offset + params["limit"]])


def test_pagination_resume_cache_and_explicit_refresh(tmp_path, monkeypatch):
    import ashare.adapters.data.tushare_provider as module

    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    api = FakeAPI()
    provider = TushareProvider(api=api, pause=0)
    api.fail = True
    with pytest.raises(AshareError) as error:
        provider.fetch_pages(tmp_path, "daily", {"trade_date": "20250430"}, page_size=2)
    assert "NEVER_PRINT" not in str(error.value)
    assert list(tmp_path.rglob("page-000000.json"))
    api.fail = False
    api.calls = []
    result = provider.fetch_pages(tmp_path, "daily", {"trade_date": "20250430"}, page_size=2)
    assert result["rows"] == [{"id": 0}, {"id": 1}, {"id": 2}] and api.calls == [2]
    api.calls = []
    again = provider.fetch_pages(tmp_path, "daily", {"trade_date": "20250430"}, page_size=2)
    assert again == result and not api.calls
    fresh = provider.fetch_pages(
        tmp_path, "daily", {"trade_date": "20250430"}, page_size=2, refresh=True
    )
    assert fresh["batch_id"] != result["batch_id"] and api.calls == [0, 2]


def test_non_paginating_endpoint_never_claims_complete(tmp_path):
    api = FakeAPI()
    api.repeat = True
    provider = TushareProvider(api=api, pause=0)
    with pytest.raises(AshareError, match="repeats page"):
        provider.fetch_pages(tmp_path, "daily", {}, page_size=2)
    assert not list(tmp_path.rglob("manifest.json"))


def test_credentials_cannot_be_recorded_in_request(tmp_path):
    provider = TushareProvider(api=FakeAPI(), pause=0)
    with pytest.raises(AshareError, match="credentials"):
        provider.fetch_pages(tmp_path, "daily", {"token": "secret"}, page_size=2)
    assert not list(tmp_path.rglob("request.json"))
