package dev.selfevolving.benchmark;

public class RangeLabels {
    public String build(int[] values) {
        StringBuilder labels = new StringBuilder();
        // Intentional defect: the loop also visits the first invalid array index.
        for (int index = 0; index <= values.length; index++) {
            if (index > 0) {
                labels.append(",");
            }
            labels.append(values[index]);
        }
        return labels.toString();
    }
}
