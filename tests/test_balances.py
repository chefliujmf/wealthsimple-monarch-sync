from datetime import date

from wsmsync.balances import fill_days


def test_fill_carries_forward_and_uses_earlier_point():
    pts = {"2026-10-12": 150.0, "2026-10-22": 140.00, "2026-10-29": 125.00}
    out = fill_days(pts, date(2026, 10, 19), date(2026, 10, 30))
    assert out["2026-10-19"] == 150.0          # from the point before the range
    assert out["2026-10-22"] == 140.00
    assert out["2026-10-28"] == 140.00         # carried
    assert out["2026-10-30"] == 125.00
    assert len(out) == 12


def test_card_owed_rebuilt_backward_from_today():
    from wsmsync.balances import card_daily_owed
    settled = [("2026-10-20", -100.0), ("2026-10-26", 2000.00), ("2026-10-27", -54.0)]
    out = card_daily_owed(1500.00, settled, date(2026, 10, 19), date(2026, 10, 28))
    assert out["2026-10-28"] == 1500.00          # today
    assert out["2026-10-26"] == round(1500.00 - 54.0, 2)            # before the 27th purchase
    assert out["2026-10-25"] == round(1500.00 - 54.0 + 2000.00, 2)  # before the payment
    assert out["2026-10-19"] == round(1500.00 - 54.0 + 2000.00 - 100.0, 2)


def test_card_debt_goes_to_monarch_as_negative():
    from wsmsync.balances import monarch_card_balance
    assert monarch_card_balance({"2026-10-01": 500.0, "2026-10-02": 0.0, "2026-10-03": -20.0}) == \
        {"2026-10-01": -500.0, "2026-10-02": 0.0, "2026-10-03": 20.0}   # owed, zero, card in credit
