from decimal import Decimal

from rqalpha.const import INSTRUMENT_TYPE, SIDE
from rqalpha.core.events import EVENT
from rqalpha.environment import Environment
from rqalpha.interface import AbstractMod, AbstractTransactionCostDecider, TransactionCost

from ashare.accounting.fees import estimate_fees
from ashare.config import Fees
from ashare.data.snapshot import Store
from ashare.domain.models import decimal, money

from .rqalpha_data import CanonicalDataSource


class ScenarioCostDecider(AbstractTransactionCostDecider):
    def __init__(self, fees, multiplier):
        self.fees, self.multiplier, self.gross, self.charged = fees, decimal(multiplier), {}, {}

    def calc(self, args):
        asset = "stock" if args.instrument.type == INSTRUMENT_TYPE.CS else "etf"
        day = Environment.get_instance().trading_dt.date()
        gross = decimal(args.price) * args.quantity
        if args.order_id is None:
            values = estimate_fees(
                self.fees,
                asset,
                "BUY" if args.side == SIDE.BUY else "SELL",
                gross,
                day,
                self.multiplier,
            )
        else:
            oid = args.order_id
            cumulative = self.gross.get(oid, Decimal(0)) + gross
            new_commission = money(
                max(self.fees.minimum_commission, cumulative * self.fees.commission_rate)
                * self.multiplier
            )
            values = estimate_fees(
                self.fees,
                asset,
                "BUY" if args.side == SIDE.BUY else "SELL",
                gross,
                day,
                self.multiplier,
            )
            values["commission"] = new_commission - self.charged.get(oid, Decimal(0))
            self.gross[oid], self.charged[oid] = cumulative, new_commission
        return TransactionCost(**{k: float(v) for k, v in values.items()})


class SnapshotMod(AbstractMod):
    def start_up(self, env, mod_config):
        from pathlib import Path

        snapshot, tables = Store(Path(mod_config.root)).load(mod_config.snapshot_id)
        env.set_data_source(CanonicalDataSource(snapshot, tables))
        decider = ScenarioCostDecider(
            Fees.model_validate(mod_config.fees), mod_config.cost_multiplier
        )
        for asset in (INSTRUMENT_TYPE.CS, INSTRUMENT_TYPE.ETF):
            env.set_transaction_cost_decider(asset, decider)

        def effective(event):
            env.config.mod.northstar.effective_matching_type = str(
                env.config.mod.sys_simulation.matching_type
            )

        env.event_bus.add_listener(EVENT.POST_SYSTEM_INIT, effective)

    def tear_down(self, code, exception=None):
        pass


def load_mod():
    return SnapshotMod()
