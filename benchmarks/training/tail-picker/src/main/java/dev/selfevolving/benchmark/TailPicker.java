package dev.selfevolving.benchmark;

public class TailPicker {
    public int last(int[] values) {
        // Intentional training defect: this index is outside the array.
        return values[values.length];
    }
}
