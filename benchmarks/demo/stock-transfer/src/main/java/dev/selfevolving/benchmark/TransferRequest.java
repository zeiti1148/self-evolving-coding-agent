package dev.selfevolving.benchmark;

public record TransferRequest(String sku, int quantity) {
    public void validate() {
        if (sku == null || sku.isBlank() || quantity < 0) {
            throw new IllegalArgumentException("Invalid transfer request");
        }
    }
}
