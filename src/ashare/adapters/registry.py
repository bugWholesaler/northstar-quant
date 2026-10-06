"""Explicit adapters, initialized without credentials or network side effects."""

from ashare.adapters.backtest.rqalpha_engine import RQAlphaEngine
from ashare.adapters.data.baostock_provider import BaoStockProvider
from ashare.adapters.data.csv_provider import CSVProvider
from ashare.adapters.data.tushare_provider import TushareProvider
from ashare.adapters.execution.manual_csv import ManualCSVExecution
from ashare.ports.interfaces import Registry
from ashare.strategies.rules import BuyHold, ETFTrend, StockEqualWeight, StockLowVol

DATA_PROVIDERS, ENGINES, STRATEGIES, EXECUTIONS = (Registry() for _ in range(4))
DATA_PROVIDERS.register("csv", CSVProvider)
DATA_PROVIDERS.register("tushare", TushareProvider)
DATA_PROVIDERS.register("baostock", BaoStockProvider)
ENGINES.register("rqalpha", RQAlphaEngine)
STRATEGIES.register("etf_trend", ETFTrend)
STRATEGIES.register("stock_lowvol", StockLowVol)
STRATEGIES.register("buy_hold", BuyHold)
STRATEGIES.register("stock_equal", StockEqualWeight)
EXECUTIONS.register("manual_csv", ManualCSVExecution)


# Paper invokes the same daily orchestrator; lazy import avoids an import cycle.
def paper_adapter():
    from ashare.adapters.execution.paper import PaperExecution

    return PaperExecution()


EXECUTIONS.register("paper", paper_adapter)
