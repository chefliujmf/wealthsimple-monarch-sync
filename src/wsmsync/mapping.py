"""Turn a Wealthsimple activity into a Monarch transaction (or decide to skip it).

Pure functions, no I/O, so the rules are unit-testable without any account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Pending card holds get a new id when they settle; importing them would double-count.
PENDING_STATUSES = {"authorized", "pending"}
# Money that never moved (e.g. a rejected e-transfer an aggregator imported as real).
FAILED_STATUSES = {"rejected", "cancelled", "canceled", "expired", "failed", "reversed"}

# Investment-internal movements that are not budget events. Only cash in/out of an
# account (deposits, withdrawals, dividends, interest, fees) belongs in a budget.
TRADE_TYPES = {"DIY_BUY", "DIY_SELL", "MANAGED_BUY", "MANAGED_SELL", "CRYPTO_BUY", "CRYPTO_SELL",
               "OPTIONS_BUY", "OPTIONS_SELL", "CORPORATE_ACTION", "STOCK_DIVIDEND", "FUNDS_CONVERSION"}

BUY_TYPES = {"DIY_BUY", "MANAGED_BUY", "CRYPTO_BUY", "OPTIONS_BUY"}
SELL_TYPES = {"DIY_SELL", "MANAGED_SELL", "CRYPTO_SELL", "OPTIONS_SELL"}


@dataclass(frozen=True)
class MonarchTxn:
    ws_id: str
    date: str          # YYYY-MM-DD, local
    amount: float      # Monarch convention: negative = money out
    merchant: str
    notes: str
    category: str | None = None  # Monarch category name; None = the profile default


# Only money that has definitively moved is imported. Wealthsimple's unifiedStatus is the
# authority (observed: COMPLETED, PENDING, IN_PROGRESS, CANCELLED, REJECTED, REVERSED); anything
# not COMPLETED can still change or vanish (card holds, e-transfers in progress), so wait for it.
FINAL_UNIFIED = {"COMPLETED"}
FINAL_STATUS = {"completed", "settled", ""}


def is_final(act: dict) -> bool:
    status = (act.get("status") or "").lower()
    if status in PENDING_STATUSES or status in FAILED_STATUSES:
        return False
    unified = (act.get("unifiedStatus") or "").upper()
    if unified:
        return unified in FINAL_UNIFIED
    return status in FINAL_STATUS


def is_failed(act: dict) -> bool:
    return ((act.get("status") or "").lower() in FAILED_STATUSES
            or (act.get("unifiedStatus") or "").lower() in FAILED_STATUSES)


def _symbol(act: dict) -> str:
    return (act.get("assetSymbol") or "").strip()


def investment_name(act: dict) -> tuple[str, str | None] | None:
    """Merchant + category for investment activity, in the style MX/Plaid used in Monarch
    (MX's format: "VFV Invested Cash"/Buy, "VFV Sold Asset"/Sell,
    "VAB Dividend"/Dividends & Capital Gains, "Management Fee"/Investment Fee, ...)."""
    kind, sub, sym = act.get("type"), act.get("subType"), _symbol(act)
    if kind in BUY_TYPES:
        return (f"{sym} Invested Cash".strip(), "Buy")
    if kind in SELL_TYPES:
        return (f"{sym} Sold Asset".strip(), "Sell")
    if kind == "DIVIDEND":
        return (f"{sym} Dividend".strip(), "Dividends & Capital Gains")
    if kind == "FEE" and sub == "MANAGEMENT_FEE":
        return ("Management Fee", "Investment Fee")
    if kind == "NON_RESIDENT_TAX":
        return ("Non Resident Tax", "Investment Fee")
    if kind == "REIMBURSEMENT" and sub == "ETF_REBATE":
        return ("Reimbursement Exchange Traded Fund Rebate", "Investment Fee")
    if kind == "STOCK_LENDING" or (sub or "").upper().startswith("STOCK_LENDING"):
        return ("Earnings Stock Lending", "Dividends & Capital Gains")
    if kind == "INTEREST":
        return ("Interest", "Interest")
    return None


def _transfer_name(act: dict, account_names: dict[str, str]) -> str | None:
    """Internal WS transfers: name the other account instead of showing its raw id."""
    if act.get("type") not in ("INTERNAL_TRANSFER", "LEGACY_INTERNAL_TRANSFER"):
        return None
    direction = "to" if (act.get("amountSign") or "").lower() == "negative" else "from"
    other = account_names.get(act.get("opposingAccountId") or "")
    return f"Transfer {direction} {other}" if other else f"Transfer {direction} Wealthsimple account"


def _eft_name(act: dict) -> str | None:
    if act.get("subType") != "EFT":
        return None
    direction = "to" if act.get("type") == "WITHDRAWAL" else "from"
    bank = (act.get("institutionName") or "").strip()
    acct = (act.get("redactedExternalAccountNumber") or "").strip()
    if not acct and (m := re.search(r"\*{2,}\d+", act.get("description") or "")):
        acct = m.group(0)
    target = " ".join(x for x in (bank, acct) if x) or "bank account"
    return f"EFT {direction} {target}"


def merchant_for(act: dict, account_names: dict[str, str] | None = None) -> str:
    """Best human-readable counterparty, falling back to ws-api's generated description."""
    account_names = account_names or {}
    for named in (_transfer_name(act, account_names), _eft_name(act)):
        if named:
            return named
    for key in ("spendMerchant", "billPayPayeeNickname", "billPayCompanyName", "eTransferName",
                "p2pHandle", "aftOriginatorName", "counterPartyName", "institutionName"):
        value = (act.get(key) or "").strip()
        if value:
            return value
    if act.get("type") in ("DIVIDEND", "INTEREST") and _symbol(act):
        return f"{act['type'].title()} {_symbol(act)}"
    description = (act.get("description") or act.get("type") or "Wealthsimple").strip()
    return description.replace(" None ", " ").replace(" None", "")


def signed_amount(act: dict) -> float:
    amount = abs(float(act.get("amount") or 0))
    return -amount if (act.get("amountSign") or "").lower() == "negative" else amount


def to_monarch(act: dict, mode: str, tz: str, account_names: dict[str, str] | None = None) -> MonarchTxn | None:
    """Return the Monarch transaction for this activity, or None to skip it.

    mode: "transactions" = every cash event (chequing, credit card);
          "investments"  = every activity incl. trades, named/categorised like MX/Plaid did;
          "cash_flows"   = investment account: deposits/withdrawals/dividends/fees only, no trades.
    """
    if not is_final(act):
        return None
    if mode == "cash_flows" and act.get("type") in TRADE_TYPES:
        return None
    amount = signed_amount(act)
    if amount == 0:
        return None
    occurred = datetime.fromisoformat(act["occurredAt"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
    currency = act.get("currency") or "CAD"
    note_bits = [f"ws:{act['canonicalId']}", f"{act.get('type')}/{act.get('subType')}"]
    if currency != "CAD":
        note_bits.append(currency)
    merchant, category = merchant_for(act, account_names), None
    if mode == "investments" and (named := investment_name(act)):
        merchant, category = named
        # Buys spend the account's cash, sells return it (as MX recorded them);
        # WS reports both as positive amounts.
        if act.get("type") in BUY_TYPES:
            amount = -abs(amount)
        elif act.get("type") in SELL_TYPES:
            amount = abs(amount)
    if act.get("type") == "DEPOSIT" and act.get("subType") == "CHEQUE":
        merchant = "Cheque deposit"
    return MonarchTxn(
        ws_id=act["canonicalId"],
        date=occurred.date().isoformat(),
        amount=round(amount, 2),
        merchant=merchant[:100],
        notes=" ".join(note_bits),
        category=category,
    )
