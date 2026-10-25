package dev.selfevolving.benchmark;

import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class CheckoutTest {
    private final DiscountPolicy policy = new DiscountPolicy();
    private final CheckoutService checkout = new CheckoutService();

    @Test void lineMultipliesQuantity() {
        assertEquals(4500, new CartLine("book", 1500, 3).subtotalCents());
    }
    @Test void lineRejectsNonpositiveQuantity() {
        assertThrows(IllegalArgumentException.class, () -> new CartLine("book", 100, 0).subtotalCents());
        assertThrows(IllegalArgumentException.class, () -> new CartLine("book", 100, -2).subtotalCents());
    }
    @Test void lineRejectsNegativePrice() {
        assertThrows(IllegalArgumentException.class, () -> new CartLine("book", -1, 1).subtotalCents());
    }
    @Test void discountKeepsFractionBeforeRounding() {
        assertEquals(2000, policy.discountCents(10000, 20));
        assertEquals(33, policy.discountCents(101, 33));
    }
    @Test void zeroDiscountIsZero() { assertEquals(0, policy.discountCents(10000, 0)); }
    @Test void fullDiscountEqualsSubtotal() { assertEquals(10000, policy.discountCents(10000, 100)); }
    @Test void invalidDiscountIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> policy.discountCents(10000, -1));
        assertThrows(IllegalArgumentException.class, () -> policy.discountCents(10000, 101));
    }
    @Test void checkoutCombinesMultipleLines() {
        assertEquals(5600, checkout.totalCents(List.of(new CartLine("book", 1500, 2), new CartLine("pen", 500, 4)), 0));
    }
    @Test void discountedTotalControlsShipping() {
        assertEquals(9400, checkout.totalCents(List.of(new CartLine("kit", 11000, 1)), 20));
    }
    @Test void exactShippingThresholdIsFree() {
        assertEquals(10000, checkout.totalCents(List.of(new CartLine("kit", 12500, 1)), 20));
    }
    @Test void emptyCartHasNoShipping() { assertEquals(0, checkout.totalCents(List.of(), 0)); }
    @Test void quantityDiscountAndShippingWorkTogether() {
        assertEquals(4650, checkout.totalCents(List.of(new CartLine("book", 1500, 3)), 10));
    }
}
