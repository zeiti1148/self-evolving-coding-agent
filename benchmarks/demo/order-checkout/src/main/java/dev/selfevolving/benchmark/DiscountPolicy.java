package dev.selfevolving.benchmark;

public class DiscountPolicy {
    public long discountCents(long subtotalCents, int percent) {
        return subtotalCents * (percent / 100);
    }
}
