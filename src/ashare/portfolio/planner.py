from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from statistics import median

from ashare.accounting.fees import estimate_fees
from ashare.domain.errors import AshareError
from ashare.domain.models import OrderIntent, stable_id


def plan(target, view, account, config, valid_date=None):
    if account.mode != config.mode or account.account_id != config.account_id:
        raise AshareError("MODE_ISOLATION", "planner config and account differ")
    day = valid_date or target.trade_not_before
    blockers, skipped, intents = [], [], []
    if not target.trade_not_before <= day <= target.expires_at:
        blockers.append("TARGET_EXPIRED")
    if account.reconciliation != "MATCHED":
        blockers.append("ACCOUNT_NOT_RECONCILED")
    if account.risk_state != "RUNNING":
        blockers.append("RISK_" + account.risk_state)
    blockers.extend(config.manual_blockers())
    if config.mode.value == "manual" and view.snapshot.scope == "synthetic_sample":
        blockers.append("SYNTHETIC_DATA_IN_MANUAL")
    if account.pending_orders:
        blockers.extend("UNSETTLED_ORDER:" + o["symbol"] for o in account.pending_orders)
    if sum(target.weights.values()) > config.risk.total_weight:
        blockers.append("TOTAL_WEIGHT")
    for symbol, weight in target.weights.items():
        if symbol not in view.instruments:
            blockers.append("UNKNOWN_INSTRUMENT:" + symbol)
            continue
        inst = view.instruments[symbol]
        cap = config.risk.etf_weight if inst.asset_type == "etf" else config.risk.stock_weight
        if weight > cap:
            blockers.append("SINGLE_WEIGHT:" + symbol)
    pid = stable_id(
        {
            "target": target.model_dump(mode="json"),
            "account": account.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "date": day,
        }
    )
    nav = account.cash_total + account.receivables
    for symbol, position in account.positions.items():
        bars = view.bars(symbol, 1)
        if not bars:
            blockers.append("VALUATION_MISSING:" + symbol)
        else:
            nav += position.quantity * bars[-1].close
    budget = min(
        account.cash_available,
        max(Decimal(0), nav * (1 - config.risk.cash_reserve) - (nav - account.cash_total)),
    )
    for symbol in sorted(set(target.weights) | set(account.positions)):
        bars = view.bars(symbol, max(1, config.liquidity_window))
        if not bars or bars[-1].trade_date != target.decision_at.date():
            blockers.append("STALE_PRICE:" + symbol)
            continue
        inst, bar = view.instruments[symbol], bars[-1]
        current = account.positions.get(symbol)
        have = current.quantity if current else 0
        want = int(nav * target.weights.get(symbol, Decimal(0)) / bar.close / inst.lot) * inst.lot
        difference = want - have
        if not difference:
            continue
        side = "BUY" if difference > 0 else "SELL"
        if bar.suspended or (side == "BUY" and (bar.st or bar.delisting_period)):
            skipped.append({"symbol": symbol, "reason": "INELIGIBLE_STATUS"})
            continue
        price = bar.close * (
            1 + config.risk.price_protection if side == "BUY" else 1 - config.risk.price_protection
        )
        price = (price / inst.tick).to_integral_value(
            rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR
        ) * inst.tick
        quantity = abs(difference)
        if side == "SELL":
            quantity = min(quantity, current.sellable if current else 0)
            if want:
                quantity = quantity // inst.lot * inst.lot
        fees = sum(
            estimate_fees(config.fees, inst.asset_type, side, quantity * price, day).values()
        )
        if side == "BUY":
            while quantity and quantity * price + fees > budget:
                quantity -= inst.lot
                fees = sum(
                    estimate_fees(
                        config.fees, inst.asset_type, side, quantity * price, day
                    ).values()
                )
        if quantity <= 0:
            skipped.append({"symbol": symbol, "reason": "CASH_OR_SELLABLE_BELOW_LOT"})
            continue
        if (
            len(bars) < config.liquidity_window
            or quantity * price
            > median(b.amount for b in bars[-config.liquidity_window :])
            * config.risk.liquidity_fraction
        ):
            blockers.append("LIQUIDITY:" + symbol)
        if side == "BUY":
            budget -= quantity * price + fees
        intents.append(
            OrderIntent(
                intent_id=stable_id([account.account_id, target.target_id, day, symbol, side, pid]),
                plan_id=pid,
                account_id=account.account_id,
                target_id=target.target_id,
                instrument_id=symbol,
                side=side,
                quantity=quantity,
                limit_price=price,
                reference_price=bar.close,
                valid_date=day,
                estimated_fees=fees,
                reason=target.reasons.get(symbol, "target exit"),
            )
        )
    return {
        "schema_version": "1.0",
        "plan_id": pid,
        "account_id": account.account_id,
        "mode": account.mode.value,
        "account_hash": account.event_hash,
        "snapshot_id": target.snapshot_id,
        "target": target.model_dump(mode="json"),
        "valid_date": str(day),
        "state": "BLOCKED" if blockers else "DRAFT",
        "blockers": blockers,
        "skipped": skipped,
        "intents": [i.model_dump(mode="json") for i in intents],
        "config_hash": stable_id(config),
        "nav": str(nav),
    }


def ensure_price_quantity(intent: OrderIntent, instrument, sellable=None):
    if intent.limit_price % instrument.tick:
        raise AshareError("TICK", "invalid price tick")
    if intent.side == "BUY" and intent.quantity % instrument.lot:
        raise AshareError("LOT", "buy quantity must be a round lot")
    if intent.side == "SELL" and sellable is not None and intent.quantity > sellable:
        raise AshareError("SELLABLE", "sell quantity exceeds available holdings")
