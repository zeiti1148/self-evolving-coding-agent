import pytest
from invoice import invoice_total


@pytest.mark.parametrize("items,coupon,expected", [
    ([{"unit_price": "2.675", "quantity": 1}], "0", "2.68"),
    ([{"unit_price": "4.015", "quantity": 1}], "0", "4.02"),
    ([{"unit_price": "0.335", "quantity": 3}], "0", "1.01"),
    ([{"unit_price": "2.685", "quantity": 1}], "0.010", "2.68"),
    ([{"unit_price": "2.125", "quantity": 2}], "0", "4.25"),
    ([{"unit_price": "12.50", "quantity": 2}, {"unit_price": "3.99", "quantity": 1}], "1.50", "27.49"),
    ([{"unit_price": "1.00", "quantity": 1}], "5.00", "0.00"),
    ([], "0", "0.00"),
])
def test_total(items, coupon, expected):
    assert invoice_total(items, coupon) == {"total": expected, "currency": "CNY", "lines": len(items)}


@pytest.mark.parametrize("items,coupon", [
    ([{"unit_price": "-1", "quantity": 1}], "0"),
    ([{"unit_price": "1", "quantity": 0}], "0"),
    ([{"unit_price": "1", "quantity": 1}], "-0.1"),
])
def test_reject_invalid_order(items, coupon):
    with pytest.raises(ValueError):
        invoice_total(items, coupon)
