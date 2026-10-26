package dev.selfevolving.benchmark;

public class RecipientNames {
    public String normalize(String name) {
        // Deliberate defect: missing recipients are dereferenced.
        return name.trim();
    }
}
