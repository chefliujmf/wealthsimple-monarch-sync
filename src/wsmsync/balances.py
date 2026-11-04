"""Keep Monarch balances true to Wealthsimple.

MX/Plaid used to set these accounts' balances; with that link gone, Monarch kept carrying the
last MX value forward (frozen when the aggregator broke). Transactions don't move a balance in Monarch, so we
push it ourselves through Monarch's balance-history upload (the "Upload balance history"
feature): one row per day, Wealthsimple's own end-of-day value. Re-uploading a date replaces it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def ws_daily_balances(ws, account_id: str, since: date) -> dict[str, float]:
    """{YYYY-MM-DD: CAD value} from Wealthsimple's daily historical financials."""
    out: dict[str, float] = {}
    for row in ws.get_account_historical_financials(account_id, "CAD") or []:
        day = (row.get("date") or "")[:10]
        money = row.get("netLiquidationValueV2") or row.get("netLiquidationValue") or {}
        if day and money.get("amount") is not None and day >= since.isoformat():
            out[day] = round(float(money["amount"]), 2)
    return out


def ws_current_balance(ws, account_id: str) -> float:
    for acct in ws.get_accounts():
        if acct["id"] == account_id:
            money = ((acct.get("financials") or {}).get("currentCombined") or {}).get("netLiquidationValue") or {}
            return round(float(money.get("amount") or 0), 2)
    raise RuntimeError(f"Wealthsimple account {account_id} not found")


def fill_days(points: dict[str, float], start: date, end: date) -> dict[str, float]:
    """One value per calendar day in [start, end]: days without a point (weekends, holidays,
    or before WS's daily series begins) carry the most recent earlier value forward."""
    earlier = [d for d in points if d < start.isoformat()]
    last = points[max(earlier)] if earlier else None
    out = {}
    day = start
    while day <= end:
        key = day.isoformat()
        if key in points:
            last = points[key]
        if last is not None:
            out[key] = last
        day += timedelta(days=1)
    return out


def card_owed_now(ws, account_id: str) -> float:
    """Settled amount owed on a WS credit card (pending holds excluded, like their transactions)."""
    balance = (ws.get_creditcard_account(account_id) or {}).get("balance") or {}
    return round(float(balance.get("current") or 0), 2)


def card_daily_owed(owed_now: float, settled: list[tuple[str, float]], start: date, end: date) -> dict[str, float]:
    """Rebuild end-of-day amount owed per day, backward from today's settled balance.

    WS keeps no daily history for cards. `settled` is [(local date, signed amount)] with the
    Monarch sign (purchase negative, payment/refund positive). Owed at the end of day D is
    today's owed plus every signed amount dated after D (undoing later purchases/payments).
    Returns the amount owed as a positive number; see `monarch_card_balance` for what Monarch wants.
    """
    out = {}
    day = start
    while day <= end:
        key = day.isoformat()
        later = sum(amount for d, amount in settled if d > key)
        out[key] = round(owed_now + later, 2)
        day += timedelta(days=1)
    return out



def monarch_card_balance(days_owed: dict[str, float]) -> dict[str, float]:
    """Monarch stores a credit card's debt as a NEGATIVE balance (owed $500 -> -500), like every
    liability it syncs itself. A positive value reads as a credit and inflates net worth.
    (MX wrote Wealthsimple card balances as positive; that was wrong, don't copy it.)"""
    return {d: -v for d, v in days_owed.items()}


async def push_history(mm, monarch_account_id: str, account_name: str, days: dict[str, float]) -> bool:
    from monarchmoney.monarchmoney import BalanceHistoryRow

    rows = [BalanceHistoryRow(datetime.fromisoformat(d), amount, account_name)
            for d, amount in sorted(days.items())]
    return await mm.upload_account_balance_history(monarch_account_id, rows)
