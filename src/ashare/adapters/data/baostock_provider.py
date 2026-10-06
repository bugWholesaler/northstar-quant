"""Explicit free-data fetches. Raw research bundles never become tradable snapshots."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ashare.data.snapshot import atomic_json, file_hash
from ashare.domain.errors import AshareError, Unsupported
from ashare.domain.models import SH, stable_id

DATASETS = {"daily", "calendar", "basic", "adjust_factor", "dividend"}
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus"


def vendor_symbol(symbol):
    if not re.fullmatch(r"\d{6}\.(SH|SZ)", symbol):
        raise AshareError("SYMBOL", "canonical symbol requires .SH/.SZ suffix")
    code, exchange = symbol.split(".")
    return exchange.lower() + "." + code


def request_parameters(dataset, values):
    allowed = {
        "daily": {"symbol", "start", "end", "adjustflag", "asset_type"},
        "calendar": {"start", "end"},
        "basic": {"symbol"},
        "adjust_factor": {"symbol", "start", "end"},
        "dividend": {"symbol", "year"},
    }
    if dataset not in DATASETS:
        raise Unsupported("BaoStock dataset is not registered", dataset=dataset)
    if set(values) - allowed[dataset]:
        raise AshareError("PROVIDER_PARAMETERS", "unknown/credential parameters are forbidden")
    from datetime import date

    params = {}
    if "start" in allowed[dataset]:
        start, end = date.fromisoformat(values["start"]), date.fromisoformat(values["end"])
        if start > end:
            raise AshareError("DATE_RANGE", "start follows end")
        params.update(start_date=str(start), end_date=str(end))
    if "symbol" in allowed[dataset]:
        params["code"] = vendor_symbol(values["symbol"])
    if dataset == "daily":
        asset = values.get("asset_type", "stock")
        adjustment = values.get("adjustflag", "3")
        if asset not in ("stock", "etf") or adjustment not in ("1", "2", "3"):
            raise AshareError("PROVIDER_PARAMETERS", "invalid asset/adjustment")
        params.update(
            fields=DAILY_FIELDS + (",isST" if asset == "stock" else ""),
            frequency="d",
            adjustflag=adjustment,
        )
    if dataset == "dividend":
        year = int(values["year"])
        if not 2007 <= year <= datetime.now(SH).year:
            raise AshareError("DATE_RANGE", "unsupported dividend year")
        params.update(year=str(year), yearType="operate")
    return params


class BaoStockProvider:
    def __init__(self, transport=None, timeout=30):
        if not 1 <= timeout <= 120:
            raise AshareError("TIMEOUT", "timeout must be 1..120 seconds")
        self.transport, self.timeout = transport, timeout

    def _request(self, dataset, params):
        if self.transport:
            return self.transport(dataset, params)
        try:
            installed = version("baostock")
        except PackageNotFoundError:
            raise AshareError(
                "MISSING_DEPENDENCY", "run uv sync --frozen --extra baostock"
            ) from None
        if installed != "0.9.4":
            raise AshareError("PROVIDER_VERSION", "BaoStock adapter requires pinned 0.9.4")
        payload = {
            "dataset": dataset,
            "parameters": params,
            "socket_timeout": min(self.timeout, 15),
        }
        try:
            process = subprocess.run(
                [sys.executable, "-m", "ashare.adapters.data.baostock_worker"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AshareError(
                "PROVIDER_TIMEOUT",
                "BaoStock request timed out; no source fallback",
                dataset=dataset,
            ) from None
        if process.returncode:
            raise AshareError("PROVIDER_PROCESS", "BaoStock worker failed", dataset=dataset)
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError:
            raise AshareError("PROVIDER_PROTOCOL", "invalid BaoStock worker response") from None
        if result.get("status") != "SUCCESS":
            raise AshareError(
                result.get("code", "PROVIDER_REQUEST_FAILED"),
                "BaoStock did not return a complete successful response",
                dataset=dataset,
            )
        return result

    def fetch(self, root: Path, dataset: str, parameters: dict, refresh=False):
        from ashare.ops.locks import account_lock

        params = request_parameters(dataset, parameters)
        request_id = stable_id(
            {
                "provider": "baostock",
                "sdk_version": "0.9.4",
                "dataset": dataset,
                "parameters": params,
            }
        )
        with account_lock(root, "data-baostock-" + request_id):
            return self._fetch(root, dataset, parameters, refresh)

    def _fetch(self, root: Path, dataset: str, parameters: dict, refresh=False):
        params = request_parameters(dataset, parameters)
        request = {
            "provider": "baostock",
            "sdk_version": "0.9.4",
            "dataset": dataset,
            "parameters": params,
        }
        request_id = stable_id(request)
        directory = root / "raw" / "baostock" / dataset / request_id
        if refresh:
            directory /= stable_id({"refresh_at": datetime.now(SH).isoformat()})
        path = directory / "response.json"
        if path.exists():
            integrity = json.loads((directory / "integrity.json").read_text())
            if file_hash(path) != integrity["response_sha256"]:
                raise AshareError("RAW_HASH", "BaoStock cache was modified")
            return {**json.loads(path.read_text()), "directory": str(directory), "cached": True}
        atomic_json(directory / "request.json", request)
        try:
            response = self._request(dataset, params)
            if len(response["fields"]) != len(set(response["fields"])) or any(
                set(row) != set(response["fields"]) for row in response["rows"]
            ):
                raise AshareError("PROVIDER_SCHEMA", "inconsistent BaoStock fields/rows")
            response = {
                "request_id": request_id,
                "observed_at": datetime.now(SH).isoformat(),
                **response,
            }
            atomic_json(path, response)
            atomic_json(directory / "integrity.json", {"response_sha256": file_hash(path)})
            return {**response, "directory": str(directory), "cached": False}
        except AshareError as error:
            atomic_json(directory / "failure.json", error.result())
            raise

    def probe(self, root: Path):
        requests = {
            "stock_daily": (
                "daily",
                {
                    "symbol": "600000.SH",
                    "start": "2026-09-21",
                    "end": "2026-09-30",
                    "asset_type": "stock",
                },
            ),
            "etf_daily": (
                "daily",
                {
                    "symbol": "510300.SH",
                    "start": "2026-09-21",
                    "end": "2026-09-30",
                    "asset_type": "etf",
                },
            ),
            "calendar": ("calendar", {"start": "2026-09-21", "end": "2026-10-16"}),
            "stock_basic": ("basic", {"symbol": "600000.SH"}),
            "etf_basic": ("basic", {"symbol": "510300.SH"}),
            "stock_factor": (
                "adjust_factor",
                {"symbol": "600000.SH", "start": "2025-01-01", "end": "2026-09-30"},
            ),
            "etf_factor": (
                "adjust_factor",
                {"symbol": "510300.SH", "start": "2025-01-01", "end": "2026-09-30"},
            ),
            "stock_dividend": ("dividend", {"symbol": "600000.SH", "year": "2026"}),
            "etf_dividend": ("dividend", {"symbol": "510300.SH", "year": "2026"}),
        }
        report = {
            "provider": "baostock",
            "sdk_version": "0.9.4",
            "source_ready": False,
            "datasets": {},
            "missing": [
                "verified_daily_price_limits",
                "historical_delisting_period",
                "ETF_action_completeness",
                "engine_corporate_action_parity",
            ],
        }
        for name, (dataset, params) in requests.items():
            try:
                result = self.fetch(root, dataset, params)
                report["datasets"][name] = {
                    "status": "SAMPLE_ONLY" if result["rows"] else "EMPTY_UNVERIFIED",
                    "rows": len(result["rows"]),
                    "fields": result["fields"],
                    "request_id": result["request_id"],
                }
            except AshareError as error:
                report["datasets"][name] = {"status": "FAILED", "error": error.code}
        atomic_json(root / "baostock-capabilities.json", report)
        return report

    def sync(self, config, root: Path | None = None):
        from ashare.data.baostock_learning import fetch_bundle

        return fetch_bundle(self, root or Path(config.runtime), config)
