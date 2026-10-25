package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class CountParserTest {
    private final CountParser parser = new CountParser();

    @Test void parsesNonnegativeWholeNumbers() {
        assertEquals(42, parser.parse(" 42 "));
        assertEquals(0, parser.parse("0"));
        assertEquals(Integer.MAX_VALUE, parser.parse("2147483647"));
    }

    @Test void negativeCountBecomesZero() {
        assertEquals(0, parser.parse(" -12 "));
    }

    @Test void missingTextBecomesZero() {
        assertEquals(0, parser.parse(null));
    }

    @Test void blankTextBecomesZero() {
        assertEquals(0, parser.parse(""));
        assertEquals(0, parser.parse(" \t "));
    }

    @Test void malformedOrOutOfRangeTextBecomesZero() {
        assertEquals(0, parser.parse("many"));
        assertEquals(0, parser.parse("1.5"));
        assertEquals(0, parser.parse("2147483648"));
    }
}
