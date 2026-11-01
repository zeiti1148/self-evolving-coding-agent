package dev.selfevolving.benchmark;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class ServiceTest {
    private final Names names = new Names();
    private final Quantities quantities = new Quantities();
    private final Items items = new Items();
    private final Service service = new Service();
    @Test void missingName() { assertEquals("visitor", names.normalize(null)); }
    @Test void blankName() { assertEquals("visitor", names.normalize("  ")); }
    @Test void validName() { assertEquals("Ada", names.normalize(" Ada ")); }
    @Test void validNumber() { assertEquals(2, quantities.parse(" 2 ")); }
    @Test void malformedNumber() { assertEquals(1, quantities.parse("bad")); }
    @Test void missingNumber() { assertEquals(1, quantities.parse(null)); }
    @Test void validSelection() { assertEquals("desk", items.select(List.of("room", "desk"), 1)); }
    @Test void upperBoundary() { assertEquals("unassigned", items.select(List.of("room"), 1)); }
    @Test void missingCollection() { assertEquals("unassigned", items.select(null, 0)); }
    @Test void combinedMissing() { assertEquals("visitor|1|unassigned", service.prepare(null, "bad", null, 0)); }
    @Test void combinedBlank() { assertEquals("visitor|2|unassigned", service.prepare("", "2", List.of(), 0)); }
    @Test void combinedNegativeQuantityIsRejected() { assertThrows(IllegalArgumentException.class, () -> service.prepare(" Ada ", "-1", List.of("room"), 0)); }
}
