package dev.selfevolving.benchmark;

public class CountParser {
    public int parse(String raw) {
        // Intentional defect: invalid text is parsed without a fallback.
        return Math.max(0, Integer.parseInt(raw == null ? null : raw.trim()));
    }
}
