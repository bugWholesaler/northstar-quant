"""Bounded read-only ETF request; no automatic source switching."""

import io
import json
import sys
from contextlib import redirect_stdout


def main():
    request = json.loads(sys.stdin.read())
    try:
        import akshare
        import requests

        original = requests.sessions.Session.request

        def bounded(self, method, url, **kwargs):
            if request.get("network_route") == "direct":
                self.trust_env = False
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = 15
            return original(self, method, url, **kwargs)

        requests.sessions.Session.request = bounded
        with redirect_stdout(io.StringIO()):
            frame = akshare.fund_etf_hist_em(
                symbol=request["symbol"],
                period="daily",
                start_date=request["start"].replace("-", ""),
                end_date=request["end"].replace("-", ""),
                adjust=request["adjust"],
            )
        rows = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
        result = {"status": "SUCCESS", "rows": rows, "fields": list(frame.columns)}
    except Exception as error:
        result = {"status": "ERROR", "category": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
