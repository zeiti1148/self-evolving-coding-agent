package dev.selfevolving.benchmark;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class ServiceTest {
    private final Names names = new Names();
    private final Quantities quantities = new Quantities();
    private final Items items = new Items();
    private final Service service = new Service();
    @Test void missingName() { assertEquals("guest", names.normalize(null)); }
    @Test void blankName() { assertEquals("guest", names.normalize("  ")); }
    @Test void emptyName() { assertEquals("guest", names.normalize("")); }
    @Test void whitespaceName() { assertEquals("guest", names.normalize("\t\n")); }
    @Test void missingNumber() { assertEquals(0, quantities.parse(null)); }
    @Test void malformedNumber() { assertEquals(0, quantities.parse("bad")); }
    @Test void negativeNumber() { assertEquals(0, quantities.parse("-2")); }
    @Test void hugeNumber() { assertEquals(0, quantities.parse("1000")); }
    @Test void missingCollection() { assertEquals("none", items.select(null, 0)); }
    @Test void emptyCollection() { assertEquals("none", items.select(List.of(), 0)); }
    @Test void negativeIndex() { assertEquals("none", items.select(List.of("book"), -1)); }
    @Test void upperBoundary() { assertEquals("none", items.select(List.of("book"), 1)); }
    @Test void normalName() { assertEquals("Ada", names.normalize(" Ada ")); }
    @Test void normalNumber() { assertEquals(3, quantities.parse(" 3 ")); }
    @Test void normalSelection() { assertEquals("pen", items.select(List.of("book", "pen"), 1)); }
    @Test void combinedMissing() { assertEquals("guest|0|none", service.prepare(null, "bad", null, 0)); }
    @Test void combinedBoundary() { assertEquals("guest|0|none", service.prepare("", "-1", List.of(), 0)); }
    @Test void combinedNormal() { assertEquals("Ada|2|book", service.prepare(" Ada ", "2", List.of("book"), 0)); }
}
