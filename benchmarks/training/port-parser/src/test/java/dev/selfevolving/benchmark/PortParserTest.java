package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class PortParserTest {
    private final PortParser parser = new PortParser();

    @Test
    void parsesValidPortsIncludingBoundsAndWhitespace() {
        assertEquals(80, parser.parse("80"));
        assertEquals(443, parser.parse(" 443 "));
        assertEquals(1, parser.parse("1"));
        assertEquals(65535, parser.parse("65535"));
    }

    @Test
    void absentTextUsesDefaultPort() {
        assertEquals(8080, parser.parse(null));
    }

    @Test
    void blankTextUsesDefaultPort() {
        assertEquals(8080, parser.parse(""));
        assertEquals(8080, parser.parse(" \t "));
    }

    @Test
    void malformedTextUsesDefaultPort() {
        assertEquals(8080, parser.parse("eight"));
        assertEquals(8080, parser.parse("1.5"));
        assertEquals(8080, parser.parse("99999999999"));
    }

    @Test
    void outOfRangeNumbersUseDefaultPort() {
        assertEquals(8080, parser.parse("0"));
        assertEquals(8080, parser.parse("-1"));
        assertEquals(8080, parser.parse("65536"));
    }
}
