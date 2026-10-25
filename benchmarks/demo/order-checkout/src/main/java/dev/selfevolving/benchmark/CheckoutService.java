package dev.selfevolving.benchmark;

import java.util.List;

public class CheckoutService {
    private final DiscountPolicy discounts = new DiscountPolicy();

    public long totalCents(List<CartLine> lines, int percent) {
        long subtotal = 0;
        for (CartLine line : lines) subtotal += line.subtotalCents();
        long discounted = subtotal - discounts.discountCents(subtotal, percent);
        long shipping = subtotal >= 10000 ? 0 : 600;
        return discounted + shipping;
    }
}
