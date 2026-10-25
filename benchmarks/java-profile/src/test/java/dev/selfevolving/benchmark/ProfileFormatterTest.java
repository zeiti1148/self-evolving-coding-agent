package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class ProfileFormatterTest {
    private final ProfileFormatter formatter = new ProfileFormatter();

    @Test void formatsNormalName() {
        assertEquals("User: Alice", formatter.format(new UserProfile(" Alice ")));
    }

    @Test void handlesMissingProfile() {
        assertEquals("User: guest", formatter.format(null));
    }

    @Test void handlesMissingNestedName() {
        assertEquals("User: guest", formatter.format(new UserProfile(null)));
    }
}
