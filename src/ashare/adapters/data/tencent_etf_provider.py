"""Explicit Tencent ETF route, using its public daily price/volume/amount response."""

import json
from datetime import date, datetime
from pathlib import Path

import requests

from ashare.data.snapshot import atomic_json, file_hash
from ashare.domain.errors import AshareError
from ashare.domain.models import SH, decimal, stable_id
from ashare.ops.locks import account_lock


class TencentETFProvider:
    def __init__(self, network_route="direct"):
        self.network_route = network_route

    def fetch(self, root: Path, symbol, start, end, adjust):
        if symbol not in ("510300.SH", "510500.SH", "159915.SZ") or adjust not in ("", "hfq"):
            raise AshareError("ETF_SCOPE", "unregistered ETF or adjustment")
        code = ("sh" if symbol.endswith("SH") else "sz") + symbol[:6]
        request = {
            "provider": "tencent",
            "symbol": symbol,
            "start": str(start),
            "end": str(end),
            "adjust": adjust,
            "network_route": self.network_route,
            "endpoint": "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            "max_records": 640,
        }
        rid = stable_id(request)
        directory = root / "raw" / "tencent" / rid
        with account_lock(root, "data-tencent-" + rid):
            path = directory / "response.json"
            if path.exists():
                integrity = json.loads((directory / "integrity.json").read_text())
                if file_hash(path) != integrity["sha256"]:
                    raise AshareError("RAW_HASH", "Tencent cached source changed")
                return {**json.loads(path.read_text()), "file": str(path)}
            atomic_json(directory / "request.json", request)
            session = requests.Session()
            session.trust_env = self.network_route != "direct"
            try:
                response = session.get(
                    request["endpoint"],
                    params={
                        "_var": "northstar",
                        "param": ",".join([code, "day", str(start), str(end), "640", adjust]),
                    },
                    timeout=20,
                )
                response.raise_for_status()
                text = response.text
                offset = text.find("={")
                body = json.loads(text[offset + 1 :] if offset >= 0 else text)
                part = body["data"][code]
                field = (adjust + "day") if adjust else "day"
                if field not in part:
                    raise AshareError("ADJUSTMENT_MAPPING", "supplier ignored adjustment request")
                points = part[field]
                if not points or date.fromisoformat(points[0][0]) > start:
                    raise AshareError(
                        "HISTORY_INCOMPLETE",
                        "Tencent capped response does not cover registered warmup start",
                    )
                # A response may include earlier dates than requested; filter explicitly.
                rows = []
                for row in points:
                    if len(row) < 9:
                        raise AshareError(
                            "PROVIDER_SCHEMA", "Tencent lacks actual volume/amount columns"
                        )
                    if start <= date.fromisoformat(row[0]) <= end:
                        rows.append(
                            {
                                "日期": row[0],
                                "开盘": row[1],
                                "收盘": row[2],
                                "最高": row[3],
                                "最低": row[4],
                                "成交量": row[5],
                                "成交额": str(decimal(row[8]) * 10000),
                                "供应商": "tencent",
                            }
                        )
                result = {
                    "status": "SUCCESS",
                    "rows": rows,
                    "request_id": rid,
                    "observed_at": datetime.now(SH).isoformat(),
                    "raw_payload": body,
                }
            except Exception as error:
                atomic_json(
                    directory / "failure.json",
                    {
                        "error": getattr(error, "code", "PROVIDER_REQUEST_FAILED"),
                        "category": type(error).__name__,
                    },
                )
                if isinstance(error, AshareError):
                    raise
                raise AshareError(
                    "PROVIDER_REQUEST_FAILED",
                    "Tencent ETF request failed; no automatic source fallback",
                ) from None
            finally:
                session.close()
            atomic_json(path, result)
            atomic_json(directory / "integrity.json", {"sha256": file_hash(path)})
            return {**result, "file": str(path)}
