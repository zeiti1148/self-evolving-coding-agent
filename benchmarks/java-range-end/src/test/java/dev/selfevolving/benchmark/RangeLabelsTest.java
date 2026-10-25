package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class RangeLabelsTest {
    private final RangeLabels labels = new RangeLabels();

    @Test void emptyArrayProducesEmptyText() {
        assertEquals("", labels.build(new int[] {}));
    }

    @Test void oneValueHasNoTrailingComma() {
        assertEquals("7", labels.build(new int[] {7}));
    }

    @Test void joinsValuesInInputOrder() {
        assertEquals("2,4,6", labels.build(new int[] {2, 4, 6}));
    }

    @Test void includesZeroAndNegativeValues() {
        assertEquals("0,-3,1", labels.build(new int[] {0, -3, 1}));
    }

    @Test void preservesRepeatedValues() {
        assertEquals("5,5", labels.build(new int[] {5, 5}));
    }
}
