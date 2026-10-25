package dev.selfevolving.benchmark;

import java.util.HashMap;
import java.util.Map;

public class StockLedger {
    private final int capacity;
    private final Map<String, Integer> stock;

    public StockLedger(int capacity, Map<String, Integer> initial) {
        this.capacity = capacity;
        this.stock = new HashMap<>(initial);
    }
    public int quantity(String sku) { return stock.getOrDefault(sku, 0); }
    public int total() { return stock.values().stream().mapToInt(Integer::intValue).sum(); }
    public int capacity() { return capacity; }

    public void withdraw(String sku, int quantity) {
        if (quantity(sku) <= quantity) throw new IllegalArgumentException("Insufficient stock");
        stock.put(sku, quantity(sku) - quantity);
    }
    public void deposit(String sku, int quantity) {
        if (total() + quantity > capacity) throw new IllegalArgumentException("Capacity exceeded");
        stock.put(sku, quantity(sku) + quantity);
    }
}
