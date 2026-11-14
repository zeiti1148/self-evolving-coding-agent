package dev.selfevolving.evaluation;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ContractTest {
    @Test void case1() { assertThrows(IllegalArgumentException.class, () -> Names.normalize(null)); }
    @Test void case2() { assertThrows(IllegalArgumentException.class, () -> Names.normalize(" ")); }
    @Test void case3() { assertEquals("SKU-1", Names.normalize(" sku-1 ")); }
    @Test void case4() { assertEquals(0, Quantities.parse(null)); assertEquals(0, Quantities.parse(" ")); }
    @Test void case5() { assertThrows(IllegalArgumentException.class, () -> Quantities.parse("bad")); }
    @Test void case6() { assertThrows(IllegalArgumentException.class, () -> Quantities.parse("-1")); assertThrows(IllegalArgumentException.class, () -> Quantities.parse("501")); }
    @Test void case7() { assertEquals(10, Quantities.parse(" 10 ")); }
    @Test void case8() { assertEquals("backorder", Items.select(null, 0)); }
    @Test void case9() { assertEquals("backorder", Items.select(List.of(), 0)); }
    @Test void case10() { assertEquals("first", Items.select(List.of("first", "last"), -1)); assertEquals("first", Items.select(List.of("first"), 1)); }
    @Test void case11() { assertEquals("b", Items.select(List.of("a", "b"), 1)); }
    @Test void case12() { assertEquals("SKU-1:0:first", Service.prepare(" sku-1 ", null, List.of("first", "last"), -1)); }
}
