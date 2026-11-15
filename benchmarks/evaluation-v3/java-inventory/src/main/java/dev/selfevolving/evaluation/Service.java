package dev.selfevolving.evaluation;

public class Service {
    public static String prepare(String name, String quantity, java.util.List<String> items, int index) {
        return Names.normalize(name) + ":" + Quantities.parse(quantity) + ":" + Items.select(items, index);
    }
}
