package dev.selfevolving.benchmark;

public record GiftRequest(long points, long fee) {
    public void validate() {
        if (points < 0) throw new IllegalArgumentException("Invalid points");
    }
    public long totalDebit() { return points; }
}
