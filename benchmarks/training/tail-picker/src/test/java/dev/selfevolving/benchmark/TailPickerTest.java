package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class TailPickerTest {
    private final TailPicker picker = new TailPicker();

    @Test
    void emptyArrayUsesSentinel() {
        assertEquals(-1, picker.last(new int[] {}));
    }

    @Test
    void singletonReturnsItsOnlyValue() {
        assertEquals(7, picker.last(new int[] {7}));
    }

    @Test
    void choosesLastOfSeveralValues() {
        assertEquals(9, picker.last(new int[] {2, 4, 9}));
    }

    @Test
    void preservesNegativeLastValue() {
        assertEquals(-6, picker.last(new int[] {5, -6}));
    }

    @Test
    void preservesZeroLastValue() {
        assertEquals(0, picker.last(new int[] {0, 3, 0}));
    }
}
