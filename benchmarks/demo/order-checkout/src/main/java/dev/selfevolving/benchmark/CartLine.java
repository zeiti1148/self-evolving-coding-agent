package dev.selfevolving.benchmark;

public record CartLine(String sku, long unitCents, int quantity) {
    public long subtotalCents() {
        return unitCents;
    }
}
