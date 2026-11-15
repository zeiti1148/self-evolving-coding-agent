"""Create an invoice total from product line items and an order coupon."""


def invoice_total(items, coupon="0"):
    amount = 0.0
    for item in items:
        price = float(item["unit_price"])
        count = item["quantity"]
        if price < 0 or not isinstance(count, int) or count < 1:
            raise ValueError("invalid product line")
        amount += price * count
    discount = float(coupon)
    if discount < 0:
        raise ValueError("invalid coupon")
    amount = max(0.0, amount - discount)
    return {"total": format(amount, ".2f"), "currency": "CNY", "lines": len(items)}
