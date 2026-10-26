package dev.selfevolving.benchmark;

import java.util.List;

public class FulfillmentService {
    private final RecipientNames names = new RecipientNames();
    private final QuantityParser quantities = new QuantityParser();
    private final ItemSelector selector = new ItemSelector();

    public String prepare(String name, String quantity, List<String> items, int index) {
        return names.normalize(name) + "|" + quantities.parse(quantity) + "|" + selector.select(items, index);
    }
}
