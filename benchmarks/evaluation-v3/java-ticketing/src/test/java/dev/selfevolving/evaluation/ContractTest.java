package dev.selfevolving.evaluation;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ContractTest {
    @Test void case1() { assertEquals("attendee", Names.normalize(null)); }
    @Test void case2() { assertEquals("attendee", Names.normalize("  ")); }
    @Test void case3() { assertEquals("Ada", Names.normalize(" Ada ")); }
    @Test void case4() { assertEquals(2, Quantities.parse(null)); assertEquals(2, Quantities.parse(" ")); }
    @Test void case5() { assertEquals(2, Quantities.parse("bad")); }
    @Test void case6() { assertEquals(1, Quantities.parse("-1")); assertEquals(6, Quantities.parse("99")); }
    @Test void case7() { assertEquals(4, Quantities.parse(" 4 ")); }
    @Test void case8() { assertEquals("waitlist", Items.select(null, 0)); }
    @Test void case9() { assertEquals("waitlist", Items.select(List.of(), 0)); }
    @Test void case10() { assertEquals("waitlist", Items.select(List.of("vip"), -1)); assertEquals("waitlist", Items.select(List.of("vip"), 1)); }
    @Test void case11() { assertEquals("b", Items.select(List.of("a", "b"), 1)); }
    @Test void case12() { assertEquals("attendee:6:waitlist", Service.prepare(null, "99", List.of("vip"), 5)); }
}
