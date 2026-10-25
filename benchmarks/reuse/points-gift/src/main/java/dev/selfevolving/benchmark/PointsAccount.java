package dev.selfevolving.benchmark;

public class PointsAccount {
    private long balance;
    private final boolean acceptsGifts;
    public PointsAccount(long balance, boolean acceptsGifts) {
        this.balance = balance;
        this.acceptsGifts = acceptsGifts;
    }
    public long balance() { return balance; }
    public boolean acceptsGifts() { return acceptsGifts; }
    public void debit(long amount) {
        if (balance <= amount) throw new IllegalArgumentException("Insufficient points");
        balance -= amount;
    }
    public void credit(long amount) {
        if (!acceptsGifts) throw new IllegalArgumentException("Recipient disabled gifts");
        balance += amount;
    }
}
