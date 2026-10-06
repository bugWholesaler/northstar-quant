import json
import subprocess
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ashare.data.snapshot import atomic_json, file_hash
from ashare.domain.errors import AshareError
from ashare.domain.models import SH, stable_id
from ashare.ops.locks import account_lock


class AKShareETFProvider:
    def __init__(self, network_route="environment"):
        if network_route not in ("environment", "direct"):
            raise AshareError("NETWORK_ROUTE", "unknown explicit network route")
        self.network_route = network_route

    def fetch(self, root: Path, symbol, start, end, adjust, timeout=45):
        if symbol not in ("510300.SH", "510500.SH", "159915.SZ") or adjust not in ("", "hfq"):
            raise AshareError(
                "ETF_SCOPE", "first composite trial only admits registered ETF sample and raw/hfq"
            )
        request = {
            "provider": "akshare_eastmoney",
            "version": "1.19.1",
            "symbol": symbol,
            "start": str(start),
            "end": str(end),
            "adjust": adjust,
            "network_route": self.network_route,
        }
        rid = stable_id(request)
        directory = root / "raw" / "akshare_eastmoney" / rid
        with account_lock(root, "data-akshare-" + rid):
            path = directory / "response.json"
            if path.exists():
                integrity = json.loads((directory / "integrity.json").read_text())
                if file_hash(path) != integrity["sha256"]:
                    raise AshareError("RAW_HASH", "ETF raw cache changed")
                return {**json.loads(path.read_text()), "file": str(path)}
            try:
                if version("akshare") != "1.19.1":
                    raise AshareError("PROVIDER_VERSION", "pinned AKShare 1.19.1 required")
            except PackageNotFoundError:
                raise AshareError("MISSING_DEPENDENCY", "run uv sync --extra akshare") from None
            atomic_json(directory / "request.json", request)
            try:
                process = subprocess.run(
                    [sys.executable, "-m", "ashare.adapters.data.akshare_etf_worker"],
                    input=json.dumps({**request, "symbol": symbol[:6]}),
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                atomic_json(directory / "failure.json", {"error": "PROVIDER_TIMEOUT"})
                raise AshareError("PROVIDER_TIMEOUT", "ETF request exceeded deadline") from None
            try:
                result = json.loads(process.stdout)
            except json.JSONDecodeError:
                raise AshareError("PROVIDER_PROTOCOL", "invalid ETF worker response") from None
            if process.returncode or result.get("status") != "SUCCESS" or not result.get("rows"):
                atomic_json(directory / "failure.json", {"error": "PROVIDER_REQUEST_FAILED"})
                raise AshareError(
                    "PROVIDER_REQUEST_FAILED",
                    "ETF supplier did not provide a nonempty successful response",
                )
            result.update(observed_at=datetime.now(SH).isoformat(), request_id=rid)
            atomic_json(path, result)
            atomic_json(directory / "integrity.json", {"sha256": file_hash(path)})
            return {**result, "file": str(path)}
