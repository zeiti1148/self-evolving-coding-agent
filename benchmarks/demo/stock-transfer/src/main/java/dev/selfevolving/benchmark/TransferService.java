package dev.selfevolving.benchmark;

public class TransferService {
    public void transfer(StockLedger source, StockLedger target, TransferRequest request) {
        source.withdraw(request.sku(), request.quantity());
        target.deposit(request.sku(), request.quantity());
    }
}
