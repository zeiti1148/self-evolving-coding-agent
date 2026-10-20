package dev.selfevolving.benchmark;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GreetingServiceTest {

    private final GreetingService service = new GreetingService();

    @Test
    void greetsNormalName() {
        assertEquals("Hello, Alice!", service.greet(" Alice "));
    }

    @Test
    void greetsNullName() {
        assertEquals("Hello, guest!", service.greet(null));
    }
}
