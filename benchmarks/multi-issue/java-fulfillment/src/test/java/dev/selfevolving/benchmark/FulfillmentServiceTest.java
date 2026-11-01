package dev.selfevolving.benchmark;

import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class FulfillmentServiceTest {
    private final RecipientNames names = new RecipientNames();
    private final QuantityParser quantities = new QuantityParser();
    private final ItemSelector selector = new ItemSelector();

    @Test void nameIsTrimmed() { assertEquals("Alice", names.normalize(" Alice ")); }
    @Test void missingNameUsesGuest() { assertEquals("guest", names.normalize(null)); }
    @Test void normalQuantity() { assertEquals(3, quantities.parse(" 3 ")); }
    @Test void invalidQuantity() { assertEquals(0, quantities.parse("three")); }
    @Test void blankQuantity() { assertEquals(0, quantities.parse(" ")); }
    @Test void missingQuantity() { assertEquals(0, quantities.parse(null)); }
    @Test void negativeQuantity() { assertEquals(0, quantities.parse("-2")); }
    @Test void normalSelection() { assertEquals("pen", selector.select(List.of("book", "pen"), 1)); }
    @Test void emptySelection() { assertEquals("none", selector.select(List.of(), 0)); }
    @Test void outOfRangeSelection() { assertEquals("none", selector.select(List.of("book"), 1)); }
    @Test void negativeSelection() { assertEquals("none", selector.select(List.of("book"), -1)); }
    @Test void combinedRequest() { assertEquals("guest|0|none", new FulfillmentService().prepare(null, "bad", List.of(), 2)); }
}
