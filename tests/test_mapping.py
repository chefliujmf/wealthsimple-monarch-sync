from wsmsync.mapping import to_monarch

TZ = "America/Toronto"


def act(**kw):
    base = {"canonicalId": "ac-1", "occurredAt": "2026-10-05T03:30:00.000Z", "amount": "12.34",
            "amountSign": "negative", "currency": "CAD", "status": "settled",
            "type": "CREDIT_CARD", "subType": "PURCHASE", "spendMerchant": "METRO"}
    base.update(kw)
    return base


def test_card_purchase_is_negative_and_local_date():
    txn = to_monarch(act(), "transactions", TZ)
    assert txn.amount == -12.34
    assert txn.merchant == "METRO"
    assert txn.date == "2026-10-04"  # 03:30Z is still the 4th in Toronto
    assert txn.notes.startswith("ws:ac-1")


def test_pending_hold_is_skipped():
    assert to_monarch(act(status="authorized"), "transactions", TZ) is None


def test_refund_is_positive():
    txn = to_monarch(act(subType="REFUND", amountSign="positive"), "transactions", TZ)
    assert txn.amount == 12.34


def test_trades_skipped_in_cash_flows_but_dividends_kept():
    assert to_monarch(act(type="DIY_BUY", subType=None, spendMerchant=None), "cash_flows", TZ) is None
    div = to_monarch(act(type="DIVIDEND", subType=None, spendMerchant=None, amountSign="positive",
                         assetSymbol="VFV"), "cash_flows", TZ)
    assert div.amount == 12.34 and div.merchant == "Dividend VFV"


def test_etransfer_uses_counterparty_name():
    txn = to_monarch(act(type="WITHDRAWAL", subType="E_TRANSFER", spendMerchant=None,
                         eTransferName="Jane Doe"), "transactions", TZ)
    assert txn.merchant == "Jane Doe"


def test_usd_noted():
    assert "USD" in to_monarch(act(currency="USD"), "transactions", TZ).notes


def test_duplicate_guard_matches_aggregator_rows_once_each():
    from wsmsync.sync import claim_duplicate
    fare = lambda wid: to_monarch(act(canonicalId=wid, amount="2.09", spendMerchant="City Transit"), "transactions", TZ)
    existing = [{"id": "m1", "amount": -2.09, "date": "2026-10-04", "notes": ""},
                {"id": "m2", "amount": -2.09, "date": "2026-10-05", "notes": ""}]
    assert claim_duplicate(fare("a"), existing)["id"] == "m1"
    assert claim_duplicate(fare("b"), existing)["id"] == "m2"
    assert claim_duplicate(fare("c"), existing) is None  # third fare is genuinely new


def test_duplicate_guard_prefers_our_ws_note_and_ignores_far_dates():
    from wsmsync.sync import claim_duplicate
    txn = to_monarch(act(), "transactions", TZ)
    existing = [{"id": "far", "amount": -12.34, "date": "2026-09-01", "notes": ""},
                {"id": "ours", "amount": -99.0, "date": "2026-10-04", "notes": "ws:ac-1 CREDIT_CARD/PURCHASE"}]
    assert claim_duplicate(txn, existing)["id"] == "ours"
    assert claim_duplicate(to_monarch(act(canonicalId="z"), "transactions", TZ), existing) is None


def test_investment_naming_matches_mx_history():
    buy = to_monarch(act(type="MANAGED_BUY", subType=None, spendMerchant=None, assetSymbol="VFV"), "investments", TZ)
    assert (buy.merchant, buy.category, buy.amount) == ("VFV Invested Cash", "Buy", -12.34)
    sell = to_monarch(act(type="DIY_SELL", subType=None, spendMerchant=None, assetSymbol="VCN",
                          amountSign="positive"), "investments", TZ)
    assert (sell.merchant, sell.category) == ("VCN Sold Asset", "Sell")
    fee = to_monarch(act(type="FEE", subType="MANAGEMENT_FEE", spendMerchant=None), "investments", TZ)
    assert (fee.merchant, fee.category) == ("Management Fee", "Investment Fee")
    div = to_monarch(act(type="DIVIDEND", subType=None, spendMerchant=None, assetSymbol="VAB",
                         amountSign="positive"), "investments", TZ)
    assert (div.merchant, div.category) == ("VAB Dividend", "Dividends & Capital Gains")


def test_transfer_and_eft_names_are_clean():
    names = {"ca-cash-msb-AAAA1111": "Joint Chequing"}
    t = to_monarch(act(type="INTERNAL_TRANSFER", subType="SOURCE", spendMerchant=None,
                       opposingAccountId="ca-cash-msb-AAAA1111"), "transactions", TZ, names)
    assert t.merchant == "Transfer to Joint Chequing"
    u = to_monarch(act(type="INTERNAL_TRANSFER", subType="DESTINATION", spendMerchant=None, amountSign="positive",
                       opposingAccountId="ca-cash-msb-BBBB2222"), "transactions", TZ, names)
    assert u.merchant == "Transfer from Wealthsimple account"
    e = to_monarch(act(type="WITHDRAWAL", subType="EFT", spendMerchant=None, institutionName=None,
                       redactedExternalAccountNumber="****123"), "transactions", TZ)
    assert e.merchant == "EFT to ****123"


def test_duplicate_guard_any_sign_for_investments():
    from wsmsync.sync import claim_duplicate
    buy = to_monarch(act(type="MANAGED_BUY", subType=None, assetSymbol="VFV", amount="100.00"), "investments", TZ)
    mx_row = [{"id": "mx", "amount": 100.0, "date": "2026-10-04", "notes": ""}]
    assert claim_duplicate(buy, list(mx_row)) is None
    assert claim_duplicate(buy, list(mx_row), any_sign=True)["id"] == "mx"


def test_buys_are_money_out_even_if_ws_says_positive():
    buy = to_monarch(act(type="MANAGED_BUY", subType=None, assetSymbol="VFV", amountSign="positive",
                         spendMerchant=None), "investments", TZ)
    assert buy.amount == -12.34


def test_eft_number_recovered_from_description():
    e = to_monarch(act(type="WITHDRAWAL", subType="EFT", spendMerchant=None,
                       description="Withdrawal: EFT to None ****123"), "transactions", TZ)
    assert e.merchant == "EFT to ****123"


def test_split_parts_fold_into_parent_for_matching():
    import asyncio
    from wsmsync.sync import _merge_splits, claim_duplicate

    class FakeMonarch:
        async def get_transaction_details(self, tid):
            return {"getTransaction": {"originalTransaction": {"id": "parent", "amount": -300.0, "date": "2026-10-14"}}}

    rows = [{"id": "a", "amount": -100.0, "date": "2026-10-14", "isSplitTransaction": True},
            {"id": "b", "amount": -150.0, "date": "2026-10-14", "isSplitTransaction": True},
            {"id": "c", "amount": -50.0, "date": "2026-10-14", "isSplitTransaction": True}]
    pool = asyncio.run(_merge_splits(FakeMonarch(), rows))
    assert len(pool) == 1 and pool[0]["amount"] == -300.0
    transfer = to_monarch(act(type="INTERNAL_TRANSFER", subType="SOURCE", spendMerchant=None, amount="300.00",
                              occurredAt="2026-10-14T12:00:00.000Z"), "transactions", TZ)
    assert claim_duplicate(transfer, pool)["id"] == "parent"


def test_rejected_and_cancelled_never_imported():
    for status, unified in [("rejected", "REJECTED"), ("completed", "CANCELLED"), ("cancelled", None),
                            ("expired", None), (None, "REVERSED")]:
        a = act(type="WITHDRAWAL", subType="E_TRANSFER", spendMerchant=None, status=status, unifiedStatus=unified)
        assert to_monarch(a, "transactions", TZ) is None, (status, unified)
    ok = act(type="WITHDRAWAL", subType="E_TRANSFER", spendMerchant=None, status="completed", unifiedStatus="COMPLETED")
    assert to_monarch(ok, "transactions", TZ) is not None


def test_only_completed_money_is_imported():
    for status, unified in [("processing", "IN_PROGRESS"), ("authorized", "PENDING"), ("", "PENDING"),
                            ("completed", "IN_PROGRESS"), ("rejected", "REJECTED"), ("reversed", "REVERSED")]:
        a = act(type="DEPOSIT", subType="E_TRANSFER", spendMerchant=None, status=status, unifiedStatus=unified,
                amountSign="positive")
        assert to_monarch(a, "transactions", TZ) is None, (status, unified)
    for status, unified in [("completed", "COMPLETED"), ("settled", "COMPLETED"), (None, "COMPLETED"), ("settled", None)]:
        a = act(status=status, unifiedStatus=unified)
        assert to_monarch(a, "transactions", TZ) is not None, (status, unified)
