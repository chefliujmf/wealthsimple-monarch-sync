"""One sync pass for one profile: new Wealthsimple activity -> Monarch transactions (+ balances)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from .config import Config, Profile
from . import balances
from .mapping import is_failed, to_monarch
from .sources import monarch, wealthsimple
from .state import State

log = logging.getLogger("wsmsync")

# Re-scan a window, not just "since last run": card purchases settle days after they happen.
LOOKBACK_DAYS = 10


async def _categories(mm) -> dict[str, str]:
    return {c["name"].lower(): c["id"] for c in (await mm.get_transaction_categories())["categories"]}


def _category_id(categories: dict[str, str], name: str) -> str:
    if name.lower() not in categories:
        raise SystemExit(f"Monarch category {name!r} not found; set monarch_category in the config")
    return categories[name.lower()]


MATCH_WINDOW_DAYS = 3  # card dates drift between authorization, settlement and the aggregator


async def _existing_monarch(mm, account_id: str, since: datetime) -> list[dict]:
    """Transactions already in Monarch for this account, from any source (MX/Plaid, manual, us)."""
    start = (since - timedelta(days=MATCH_WINDOW_DAYS + 2)).date().isoformat()
    end = (datetime.now() + timedelta(days=1)).date().isoformat()
    # all_transactions: Monarch's default list omits HIDDEN transactions (e.g. a user hides
    # every transaction of a shared account), and the guard must see them or it imports duplicates.
    res = await mm.get_transactions(limit=1000, start_date=start, end_date=end, account_ids=[account_id],
                                    transaction_visibility="all_transactions")
    return await _merge_splits(mm, res["allTransactions"]["results"])


async def _merge_splits(mm, rows: list[dict]) -> list[dict]:
    """Monarch lists a split transaction as its parts (e.g. an insurance payment -300.00 split into
    Home -100.00 + Auto -200.00). Wealthsimple has one activity, so fold parts back into their parent
    for matching -- otherwise the guard sees no -300.00 and imports a duplicate."""
    parents: dict[str, dict] = {}
    out = []
    for row in rows:
        if not row.get("isSplitTransaction"):
            out.append(row)
            continue
        detail = (await mm.get_transaction_details(row["id"])).get("getTransaction") or {}
        parent = detail.get("originalTransaction") or {}
        if not parent.get("id"):
            out.append(row)
            continue
        merged = parents.setdefault(parent["id"], {
            "id": parent["id"], "amount": parent.get("amount"), "date": parent.get("date") or row["date"],
            "notes": "", "split_parts": [],
        })
        merged["split_parts"].append(row["id"])
    for merged in parents.values():
        if merged["amount"] is None:
            continue
        out.append(merged)
    return out


def claim_duplicate(txn, existing: list[dict], any_sign: bool = False) -> dict | None:
    """Find (and consume) a Monarch transaction that already represents this WS activity.

    Ours carry `ws:<id>` in notes; anything else (e.g. imported by MX/Plaid before it broke) is
    matched on exact amount within MATCH_WINDOW_DAYS (any sign for investment accounts: MX/Plaid
    recorded recent managed buys as positive). Each existing row is consumed once, so two
    identical same-day purchases (two bus fares) map to two rows, never one.
    """
    for row in existing:
        if f"ws:{txn.ws_id}" in (row.get("notes") or ""):
            existing.remove(row)
            return row
    when = datetime.fromisoformat(txn.date)
    for row in existing:
        amount = round(float(row["amount"]), 2)
        same = abs(amount) == abs(txn.amount) if any_sign else amount == txn.amount
        if same and \
                abs((datetime.fromisoformat(row["date"]) - when).days) <= MATCH_WINDOW_DAYS:
            existing.remove(row)
            return row
    return None


async def sync_profile(cfg: Config, profile: Profile, state: State, dry_run: bool,
                       limit: int | None = None, account: str | None = None) -> dict:
    ws = wealthsimple(cfg.data_dir, profile.name, profile.ws_username)
    mm = await monarch(cfg.data_dir, profile.monarch_profile or profile.name)
    categories = await _categories(mm)
    default_category = _category_id(categories, profile.monarch_category)
    account_names = {a["id"]: (a.get("nickname") or a.get("description") or a["id"]) for a in ws.get_accounts()}

    created = skipped = duplicates = 0
    reversed_after_import: list[str] = []
    for link in profile.accounts:
        if account and link.ws_account_id != account:
            continue
        # Backfill is tracked PER ACCOUNT: an account added later still imports from its own
        # start date on its first run, then switches to the rolling lookback window.
        label = (link.name or link.ws_account_id).split(" -> ")[0]
        first_day = datetime.fromisoformat(link.start_date or profile.start_date)
        marker = f"__initial_backfill__:{link.ws_account_id}"
        if state.seen(profile.name, marker):
            since = max(first_day, datetime.now() - timedelta(days=LOOKBACK_DAYS))
        else:
            since = first_day
        if link.mode != "balance_only":
            activities = ws.get_activities(link.ws_account_id, start_date=since, load_all=True)
            existing = await _existing_monarch(mm, link.monarch_account_id, since)
            for act in sorted(activities, key=lambda a: a["occurredAt"]):
                if limit is not None and created >= limit:
                    break
                if is_failed(act) and state.seen(profile.name, act["canonicalId"]):
                    # Imported while completed, later reversed/cancelled by Wealthsimple. Never
                    # delete automatically: flag it so the user decides.
                    reversed_after_import.append(f"{label} {act.get('occurredAt', '')[:10]} "
                                                 f"{act.get('amount')} {act.get('description', '')[:40]}")
                txn = to_monarch(act, link.mode, cfg.timezone, account_names)
                if txn is None or state.seen(profile.name, txn.ws_id):
                    skipped += 1
                    continue
                dup = claim_duplicate(txn, existing, any_sign=link.mode == "investments")
                if dup is not None:
                    log.info("%s [%s] DUP %s %+.2f %s = Monarch %s (%s)", profile.name, label, txn.date,
                             txn.amount, txn.merchant, dup["id"], dup.get("date"))
                    if not dry_run:
                        state.record(profile.name, txn.ws_id, dup["id"], txn.date, txn.amount)
                    duplicates += 1
                    continue
                log.info("%s [%s] %s %s %+.2f %s %s", profile.name, label, "DRY" if dry_run else "NEW",
                         txn.date, txn.amount, txn.merchant, txn.notes.split(" ", 1)[-1])
                if not dry_run:
                    res = await mm.create_transaction(
                        date=txn.date, account_id=link.monarch_account_id, amount=txn.amount,
                        merchant_name=txn.merchant, notes=txn.notes,
                        category_id=categories.get((txn.category or "").lower(), default_category),
                        update_balance=False,
                    )
                    payload = res["createTransaction"]
                    if payload.get("errors"):
                        raise RuntimeError(f"Monarch rejected {txn}: {payload['errors']}")
                    monarch_id = payload["transaction"]["id"]
                    state.record(profile.name, txn.ws_id, monarch_id, txn.date, txn.amount)
                    # The user reviews every imported transaction (category, merchant) like an
                    # aggregator import; createTransaction can't set it, so flag it right after.
                    await mm.update_transaction(transaction_id=monarch_id, needs_review=True,
                                                **({"hide_from_reports": True} if link.hide_imported else {}))
                created += 1
        if link.update_balance and limit is None:
            await _sync_balance(cfg, profile, link, label, ws, mm, state, dry_run)

        # Only a real transaction pass completes the backfill: a balance-only account switched to
        # transactions later must still import from its start_date, not just the rolling window.
        if not dry_run and limit is None and link.mode != "balance_only":
            state.record(profile.name, marker, "", "", 0)
    if reversed_after_import:
        log.warning("%s REVERSED AFTER IMPORT: %s", profile.name, "; ".join(reversed_after_import))
    return {"created": created, "duplicates": duplicates, "skipped": skipped,
            "reversed_after_import": reversed_after_import}


async def _sync_balance(cfg, profile, link, label, ws, mm, state, dry_run: bool, since=None, until=None) -> None:
    """Push Wealthsimple's real daily balances into Monarch's balance history.

    First run per account: every day from `balance_start` (e.g. when the aggregator froze it; defaults to start_date). After that:
    the last LOOKBACK_DAYS days, re-uploaded each run (idempotent: a date's value is replaced),
    so late-settling activity and today's live value stay correct.
    """
    marker = f"__balance_backfill__:{link.ws_account_id}"
    today = datetime.now().date()
    start = datetime.fromisoformat(profile.balance_start or profile.start_date).date()
    if since is not None:                     # explicit rebuild (wsmsync rebuild-balances)
        start = since
    elif state.seen(profile.name, marker):
        start = max(start, today - timedelta(days=LOOKBACK_DAYS))
    if link.ws_account_id.startswith("ca-credit-card"):
        acts = ws.get_activities(link.ws_account_id, start_date=datetime.combine(start, datetime.min.time()),
                                 load_all=True)
        settled = [(t.date, t.amount) for a in acts if (t := to_monarch(a, "transactions", cfg.timezone))]
        days = balances.monarch_card_balance(
            balances.card_daily_owed(balances.card_owed_now(ws, link.ws_account_id), settled, start, today))
    else:
        history = balances.ws_daily_balances(ws, link.ws_account_id, start - timedelta(days=45))
        days = balances.fill_days(history, start, today)
        days[today.isoformat()] = balances.ws_current_balance(ws, link.ws_account_id)
    if until is not None:                     # repair a past window only
        days = {d: v for d, v in days.items() if d <= until.isoformat()}
    if not days:
        log.warning("%s [%s] no balance data from Wealthsimple", profile.name, label)
        return
    first, last = min(days), max(days)
    log.info("%s [%s] BALANCE %s %s %.2f -> %s %.2f (%d days)", profile.name, label,
             "DRY" if dry_run else "PUSH", first, days[first], last, days[last], len(days))
    if dry_run:
        return
    name = link.name.split(" -> ")[-1] if " -> " in link.name else link.monarch_account_id
    for attempt in range(3):  # Monarch parses uploads asynchronously; a slow parse can time out
        try:
            if await balances.push_history(mm, link.monarch_account_id, name, days):
                break
        except Exception as exc:
            log.warning("%s [%s] balance upload attempt %d failed: %s", profile.name, label, attempt + 1, exc)
        await asyncio.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"Monarch balance upload did not complete for {label} after 3 attempts")
    state.record(profile.name, marker, "", "", 0)


async def rebuild_balances(cfg: Config, profile: Profile, state: State, account: str, since,
                           dry_run: bool, until=None) -> None:
    """Re-upload one account's daily balance history from `since` to `until` (default today), from Wealthsimple's
    own data. Use it to repair history an aggregator (or an older version of this sync) got wrong;
    each uploaded date replaces what Monarch had."""
    link = next((a for a in profile.accounts if a.ws_account_id == account), None)
    if link is None:
        raise SystemExit(f"{account} is not in profile {profile.name!r}")
    ws = wealthsimple(cfg.data_dir, profile.name, profile.ws_username)
    mm = await monarch(cfg.data_dir, profile.monarch_profile or profile.name)
    label = (link.name or link.ws_account_id).split(" -> ")[0]
    await _sync_balance(cfg, profile, link, label, ws, mm, state, dry_run, since=since, until=until)
