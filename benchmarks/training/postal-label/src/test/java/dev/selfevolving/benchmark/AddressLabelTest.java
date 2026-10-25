package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class AddressLabelTest {
    private final AddressLabel labels = new AddressLabel();

    @Test
    void rendersPostalCodeWithPrefix() {
        assertEquals("ZIP 10001", labels.render(new Address(10001)));
    }

    @Test
    void preservesZeroPostalCode() {
        assertEquals("ZIP 0", labels.render(new Address(0)));
    }

    @Test
    void preservesShortPostalCodeWithoutPadding() {
        assertEquals("ZIP 42", labels.render(new Address(42)));
    }

    @Test
    void absentAddressUsesUnknownLabel() {
        assertEquals("ZIP unknown", labels.render(null));
    }

    @Test
    void absentPostalCodeUsesUnknownLabel() {
        assertEquals("ZIP unknown", labels.render(new Address(null)));
    }
}
