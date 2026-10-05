"""Permission probing and resumable, explicit dataset fetches. Never falls back."""

import json
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ashare.data.snapshot import atomic_json
from ashare.domain.errors import AshareError, Unsupported
from ashare.domain.models import SH, Bar, decimal, stable_id

DATASETS = [
    "trade_cal",
    "stock_basic",
    "fund_basic",
    "etf_basic",
    "daily",
    "adj_factor",
    "fund_daily",
    "fund_adj",
    "bak_basic",
    "stock_st",
    "suspend_d",
    "stk_limit",
    "etf_limit",
    "dividend",
    "fund_div",
]


class TushareProvider:
    def __init__(self, api=None, pause=0.15):
        self.pause = pause
        if api is not None:
            self.api = api
        elif not os.environ.get("TUSHARE_TOKEN"):
            self.api = None
        else:
            try:
                import tushare
            except ImportError:
                raise AshareError(
                    "MISSING_DEPENDENCY", "install with uv sync --extra tushare"
                ) from None
            self.api = tushare.pro_api(os.environ["TUSHARE_TOKEN"])

    def probe(self):
        report = {"provider": "tushare", "source_ready": False, "datasets": {}}
        for dataset in DATASETS:
            result = {
                "status": "NOT_TESTED",
                "coverage": "NOT_TESTED",
                "pagination": "NOT_TESTED",
                "units": {"vol": "hand -> share x100", "amount": "thousand CNY -> CNY x1000"}
                if dataset in ("daily", "fund_daily")
                else "dataset-specific; samples NOT_TESTED",
            }
            if self.api is not None:
                try:
                    params = {"limit": 1}
                    if dataset in (
                        "daily",
                        "adj_factor",
                        "fund_daily",
                        "fund_adj",
                        "bak_basic",
                        "stock_st",
                        "stk_limit",
                        "etf_limit",
                    ):
                        params["trade_date"] = "20250930"
                    frame = self.api.query(dataset, **params)
                    result.update(status="SAMPLE_ONLY", rows=len(frame), fields=list(frame.columns))
                    time.sleep(self.pause)
                except Exception:
                    # SDK exception strings may include credentials/request URLs.
                    result["status"] = "FAILED_PERMISSION_OR_PROVIDER"
            report["datasets"][dataset] = result
        return report

    def fetch_pages(
        self,
        root: Path,
        dataset: str,
        parameters: dict,
        page_size=1000,
        max_pages=10000,
        refresh=False,
    ):
        if page_size < 1 or max_pages < 1:
            raise AshareError("PAGINATION_ARGUMENT", "positive page bounds required")
        if dataset not in DATASETS:
            raise Unsupported("unregistered dataset", dataset=dataset)
        if self.api is None:
            raise AshareError("TOKEN_REQUIRED", "set TUSHARE_TOKEN locally")
        if any("token" in key.lower() for key in parameters):
            raise AshareError("SECRET_ARGUMENT", "credentials cannot be request parameters")
        bid = stable_id(
            {
                "provider": "tushare",
                "dataset": dataset,
                "parameters": parameters,
                "page_size": page_size,
            }
        )
        if refresh:
            bid = stable_id({"request_id": bid, "requested_at": datetime.now(SH).isoformat()})
        directory = root / "raw" / "tushare" / dataset / bid
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(
            directory / "request.json",
            {"dataset": dataset, "parameters": parameters, "page_size": page_size},
        )
        rows, hashes = [], set()
        for page in range(max_pages):
            path = directory / f"page-{page:06d}.json"
            if path.exists():
                response = json.loads(path.read_text())
                records = response["rows"]
            else:
                for attempt in range(4):
                    try:
                        frame = self.api.query(
                            dataset, **parameters, offset=page * page_size, limit=page_size
                        )
                        records = json.loads(frame.to_json(orient="records"))
                        break
                    except Exception:
                        if attempt == 3:
                            atomic_json(
                                directory / "failure.json",
                                {"offset": page * page_size, "error": "PROVIDER_REQUEST_FAILED"},
                            )
                            raise AshareError(
                                "PROVIDER_REQUEST_FAILED",
                                "fetch failed; saved pages permit retry",
                                batch_id=bid,
                                offset=page * page_size,
                            ) from None
                        time.sleep(min(2**attempt, 8))
                atomic_json(path, {"observed_at": datetime.now(SH).isoformat(), "rows": records})
                time.sleep(self.pause)
            fingerprint = stable_id(records)
            if records and fingerprint in hashes:
                raise AshareError(
                    "PAGINATION_UNSUPPORTED",
                    "endpoint repeats page; completeness cannot be established",
                )
            hashes.add(fingerprint)
            rows.extend(records)
            if len(records) < page_size:
                atomic_json(
                    directory / "manifest.json",
                    {
                        "batch_id": bid,
                        "rows": len(rows),
                        "pages": page + 1,
                        "status": "FETCHED_NOT_SOURCE_READY",
                    },
                )
                return {"batch_id": bid, "rows": rows, "directory": str(directory)}
        raise AshareError("PAGINATION_LIMIT", "max pages reached; batch incomplete")

    @staticmethod
    def normalize_bar(row, factor, status, available_at, observed_at, availability_basis):
        required = {"suspended", "st", "delisting_period", "limit_up", "limit_down"}
        if not required <= set(status):
            raise AshareError(
                "STATUS_MISSING", "verified historical trading status and limits required"
            )
        import datetime as dt

        volume = decimal(row["vol"]) * 100
        if volume != int(volume):
            raise AshareError("VOLUME_UNIT", "nonintegral normalized volume")
        return Bar(
            instrument_id=row["ts_code"],
            trade_date=dt.datetime.strptime(row["trade_date"], "%Y%m%d").date(),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=int(volume),
            amount=decimal(row["amount"]) * 1000,
            factor=Decimal(str(factor)),
            available_at=available_at,
            observed_at=observed_at,
            availability_basis=availability_basis,
            **status,
        )

    def sync(self, config):
        raise Unsupported(
            "automatic multi-dataset historical joins require token coverage tests; use fetch + verified canonical CSV, no source-ready claim"
        )
