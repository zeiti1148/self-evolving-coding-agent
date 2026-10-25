package dev.selfevolving.benchmark;

public class PortParser {
    public int parse(String value) {
        // Intentional training defects: parsing can throw and bounds are unchecked.
        return Integer.parseInt(value == null ? null : value.trim());
    }
}
