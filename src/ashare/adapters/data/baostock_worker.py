"""Isolated anonymous data session. Parent process enforces a wall-clock deadline."""

import io
import json
import socket
import sys
from contextlib import redirect_stdout


def collect(result):
    if result.error_code != "0":
        raise ValueError("PROVIDER_QUERY_FAILED:" + str(result.error_code))
    fields = list(result.fields)
    if len(set(fields)) != len(fields):
        raise ValueError("DUPLICATE_FIELDS")
    rows = []
    while True:
        page = getattr(result, "cur_page_num", None)
        page_size = int(getattr(result, "per_page_count", 0))
        full = page_size > 0 and len(getattr(result, "data", [])) == page_size
        following = result.next()
        if result.error_code != "0":
            raise ValueError("PROVIDER_PAGE_FAILED:" + str(result.error_code))
        if not following:
            if full and getattr(result, "cur_page_num", None) == page:
                raise ValueError("PAGINATION_INCOMPLETE")
            break
        row = result.get_row_data()
        if len(row) != len(fields):
            raise ValueError("ROW_WIDTH")
        rows.append(dict(zip(fields, row)))
    return {"fields": fields, "rows": rows}


def query(api, dataset, parameters):
    methods = {
        "daily": api.query_history_k_data_plus,
        "calendar": api.query_trade_dates,
        "basic": api.query_stock_basic,
        "adjust_factor": api.query_adjust_factor,
        "dividend": api.query_dividend_data,
    }
    return collect(methods[dataset](**parameters))


def main():
    request = json.loads(sys.stdin.read())
    socket.setdefaulttimeout(request["socket_timeout"])
    try:
        import baostock as api
    except ImportError:
        print(json.dumps({"status": "ERROR", "code": "MISSING_DEPENDENCY"}))
        return
    output = None
    try:
        with redirect_stdout(io.StringIO()):
            login = api.login()  # publisher's anonymous data session, no brokerage account
            if login.error_code != "0":
                raise ValueError("PROVIDER_LOGIN_FAILED:" + str(login.error_code))
            try:
                output = query(api, request["dataset"], request["parameters"])
            finally:
                api.logout()
        output.update(status="SUCCESS")
    except Exception as error:
        # Do not serialize SDK messages containing session/server information.
        output = {
            "status": "ERROR",
            "code": "PROVIDER_REQUEST_FAILED",
            "category": type(error).__name__,
        }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
