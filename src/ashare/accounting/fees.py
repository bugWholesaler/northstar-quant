from datetime import date
from decimal import Decimal

from ashare.domain.models import money


def estimate_fees(fees, asset_type, side, gross, day, multiplier=Decimal(1)):
    commission = max(fees.minimum_commission, gross * fees.commission_rate) * multiplier
    tax = Decimal(0)
    if asset_type == "stock" and side == "SELL":
        tax = (
            gross
            * (
                fees.stamp_since_20230828
                if day >= date(2023, 8, 28)
                else fees.stamp_before_20230828
            )
            * multiplier
        )
    other = gross * fees.stock_transfer_rate * multiplier if asset_type == "stock" else Decimal(0)
    return {"commission": money(commission), "tax": money(tax), "other_fees": money(other)}


def dividend_tax_rate(acquired, sold, asset_type):
    if asset_type == "etf":
        return Decimal(0)
    if acquired is None:
        return None
    # Calendar month/year holding periods, rather than arbitrary 30/365-day buckets.
    from dateutil.relativedelta import relativedelta

    if sold <= acquired + relativedelta(months=1):
        return Decimal("0.2")
    if sold <= acquired + relativedelta(years=1):
        return Decimal("0.1")
    return Decimal(0)
