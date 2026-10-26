package dev.selfevolving.benchmark;

public class QuantityParser {
    public int parse(String text) {
        // Deliberate defect: invalid quantities are not normalized to zero.
        return Integer.parseInt(text.trim());
    }
}
