package dev.selfevolving.benchmark;

import java.util.List;

public class ItemSelector {
    public String select(List<String> items, int index) {
        // Deliberate defect: missing indexes are not handled.
        return items.get(index);
    }
}
